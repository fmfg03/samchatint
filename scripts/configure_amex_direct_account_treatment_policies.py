"""Owner-only, reviewed admission of the exact AMEX account-treatment policy.

Runtime DML stays forbidden. Apply briefly removes only the INSERT/UPDATE/DELETE
policy guard under an exclusive table lock, appends the reviewed revision and
restores the exact guard in the same transaction. No credentials are printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import stat
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from devnous.gastos.services.amex_accounting_posting_service import (
    ALLOWED_AMEX_LIABILITY_CODES,
)
from devnous.gastos.services.amex_direct_common import (
    DirectAccessError,
    DirectConflict,
    authorize,
    base_context_config,
    configured_scopes,
    digest,
)
from devnous.gastos.services.amex_direct_schema_service import (
    require_amex_direct_schema,
)

TABLE = "amex_direct_account_treatment_policies"
GUARD = "ad_account_treatment_policy_dml"
TRUNCATE_GUARD = "ad_account_treatment_policy_truncate"
CREATE_GUARD = (
    "CREATE TRIGGER ad_account_treatment_policy_dml BEFORE INSERT OR UPDATE OR DELETE "
    "ON amex_direct_account_treatment_policies FOR EACH ROW "
    "EXECUTE FUNCTION amex_direct_immutable()"
)
VERSION = "amex_direct_account_treatment_policy_preview_v1"


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _private_config(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if re.search(
                r"password|secret|token|credential|api_key|private_key|database_url",
                str(key),
                re.I,
            ):
                raise DirectConflict("policy_secret_config_field_denied")
            _private_config(child)
    elif isinstance(value, list):
        for child in value:
            _private_config(child)


def _ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise DirectConflict("policy_invalid_catalog_ids")
    try:
        result = [str(UUID(str(v))) for v in value]
    except (ValueError, TypeError, AttributeError) as exc:
        raise DirectConflict("policy_invalid_catalog_ids") from exc
    if len(result) != len(set(result)):
        raise DirectConflict("policy_duplicate_catalog_ids")
    return sorted(result)


def configured_policies() -> list[dict[str, Any]]:
    configs = configured_scopes()
    if not configs:
        raise DirectConflict("policy_explicit_configuration_required")
    bases = [digest(base_context_config(c)) for c in configs]
    if len(bases) != len(set(bases)):
        raise DirectConflict("policy_ambiguous_base_configuration")
    for config in configs:
        _private_config(config)
        _ids(config["card_account_ids"])
        seen_actors = set()
        for scope in config["actor_scopes"]:
            actor = str(UUID(scope["actor_id"]))
            if actor in seen_actors:
                raise DirectConflict("policy_ambiguous_actor_scope")
            seen_actors.add(actor)
            for key in ("benefit_budget_line_ids", "partner_debtor_account_ids"):
                own = set(_ids(scope.get(key, [])))
                if not own <= set(_ids(config.get(key, []))):
                    raise DirectConflict("policy_actor_grant_outside_context")
        for row in (config, *config["actor_scopes"]):
            pairs = row.get("benefit_budget_pairs", [])
            if not isinstance(pairs, list):
                raise DirectConflict("policy_invalid_budget_pairs")
            for pair in pairs:
                if not isinstance(pair, dict) or set(pair) != {
                    "budget_version_id",
                    "budget_concept_id",
                }:
                    raise DirectConflict("policy_invalid_budget_pairs")
                UUID(pair["budget_version_id"])
                UUID(pair["budget_concept_id"])
            if row is not config and not {canonical(p) for p in pairs} <= {
                canonical(p) for p in config.get("benefit_budget_pairs", [])
            }:
                raise DirectConflict("policy_actor_grant_outside_context")
    return sorted(configs, key=lambda c: digest(base_context_config(c)))


async def _authorize(
    session: Any,
    args: argparse.Namespace,
    configs: list[dict[str, Any]],
    *,
    locked: bool = False,
) -> None:
    for config in configs:
        await authorize(
            session,
            args.actor_id,
            "configurar_tratamientos",
            config=config,
            finance=True,
            locked=locked,
        )


async def _guard_proof(session: Any) -> dict[str, Any]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT t.tgname,t.tgtype,t.tgenabled::text AS tgenabled,p.proname,pg_get_triggerdef(t.oid,true) definition "
                    "FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid "
                    "WHERE t.tgrelid='amex_direct_account_treatment_policies'::regclass "
                    "AND NOT t.tgisinternal ORDER BY t.tgname"
                )
            )
        )
        .mappings()
        .all()
    )
    found = {r["tgname"]: dict(r) for r in rows}
    # BEFORE + ROW + INSERT/DELETE/UPDATE = 31; BEFORE + TRUNCATE statement = 34.
    if (
        set(found) != {GUARD, TRUNCATE_GUARD}
        or any(
            r["tgenabled"] != "O" or r["proname"] != "amex_direct_immutable"
            for r in rows
        )
        or found[GUARD]["tgtype"] != 31
        or found[TRUNCATE_GUARD]["tgtype"] != 34
    ):
        raise DirectConflict("policy_owner_guard_mismatch")
    body = await session.scalar(
        text("SELECT pg_get_functiondef('amex_direct_immutable()'::regprocedure)")
    )
    return {"triggers": found, "immutable_function_hash": digest(body.encode())}


async def _catalog(session: Any, config: dict[str, Any]) -> dict[str, Any]:
    cards = []
    for card_id in _ids(config["card_account_ids"]):
        row = (
            (
                await session.execute(
                    text(
                        "SELECT c.id,c.active,c.liability_cuenta_contable_id,a.codigo,a.activo "
                        "FROM amex_card_accounts c JOIN cuentas_contables a ON a.id=c.liability_cuenta_contable_id WHERE c.id=:id"
                    ),
                    {"id": UUID(card_id)},
                )
            )
            .mappings()
            .first()
        )
        if (
            not row
            or not row["active"]
            or not row["activo"]
            or row["codigo"] not in ALLOWED_AMEX_LIABILITY_CODES
        ):
            raise DirectConflict("policy_invalid_card_liability")
        cards.append(dict(row))
    debtors = []
    for account_id in _ids(config.get("partner_debtor_account_ids", [])):
        row = (
            (
                await session.execute(
                    text(
                        "SELECT id,codigo,nombre,activo,tipo FROM cuentas_contables WHERE id=:id"
                    ),
                    {"id": UUID(account_id)},
                )
            )
            .mappings()
            .first()
        )
        if (
            not row
            or not row["activo"]
            or not re.fullmatch(r"1170-002-\d{3}", row["codigo"] or "")
        ):
            raise DirectConflict("policy_invalid_partner_account")
        debtors.append(dict(row))
    lines = []
    line_ids = set(_ids(config.get("benefit_budget_line_ids", [])))
    for pair in config.get("benefit_budget_pairs", []):
        matches = (
            (
                await session.execute(
                    text(
                        "SELECT id FROM budget_lines WHERE budget_version_id=:version AND budget_concept_id=:concept"
                    ),
                    {
                        "version": UUID(pair["budget_version_id"]),
                        "concept": UUID(pair["budget_concept_id"]),
                    },
                )
            )
            .scalars()
            .all()
        )
        if not matches:
            raise DirectConflict("policy_budget_pair_has_no_lines")
        line_ids.update(str(v) for v in matches)
    for line_id in sorted(line_ids):
        row = (
            (
                await session.execute(
                    text(
                        "SELECT l.id,l.budget_version_id,l.budget_concept_id,l.tournament_id,l.tournament_code,"
                        "l.concept_name,l.line_direction,v.edition_year,v.status,v.source,bc.active,"
                        "bc.budget_direction,bc.cuenta_contable_id,a.codigo,a.activo "
                        "FROM budget_lines l JOIN budget_versions v ON v.id=l.budget_version_id "
                        "JOIN budget_concepts bc ON bc.id=l.budget_concept_id "
                        "LEFT JOIN cuentas_contables a ON a.id=bc.cuenta_contable_id WHERE l.id=:id"
                    ),
                    {"id": UUID(line_id)},
                )
            )
            .mappings()
            .first()
        )
        if (
            not row
            or row["line_direction"] != "expense"
            or row["budget_direction"] != "expense"
            or not row["active"]
            or not row["activo"]
            or not row["cuenta_contable_id"]
            or row["source"] == "distribution_revision"
            or (
                row["status"] not in {"approved", "frozen"}
                and not (
                    config.get("allow_draft_reforecast") is True
                    and row["status"] in {"draft", "reforecast"}
                )
            )
        ):
            raise DirectConflict("policy_invalid_benefit_budget_line")
        lines.append(dict(row))
    if not lines and not debtors:
        raise DirectConflict("policy_empty_treatment_catalog")
    return cast(
        dict[str, Any],
        json.loads(
            canonical({"cards": cards, "debtors": debtors, "budget_lines": lines})
        ),
    )


async def preview_policy_manifest(
    session: Any, args: argparse.Namespace, configs: list[dict[str, Any]]
) -> dict[str, Any]:
    await _authorize(session, args, configs)
    guard = await _guard_proof(session)
    plans = []
    for config in configs:
        base = base_context_config(config)
        base_hash, full_hash = digest(base), digest(config)
        previous = (
            (
                await session.execute(
                    text(
                        f"SELECT * FROM {TABLE} WHERE base_config_hash=:hash ORDER BY revision DESC LIMIT 1"
                    ),
                    {"hash": base_hash},
                )
            )
            .mappings()
            .first()
        )
        if (
            previous
            and previous["config_hash"] == full_hash
            and previous["config_text"] == canonical(config)
        ):
            raise DirectConflict("policy_configuration_already_installed")
        anchors = (
            (
                await session.execute(
                    text(
                        "SELECT c.id,c.config_hash FROM amex_statement_direct_contexts c "
                        "JOIN amex_statement_direct_state s ON s.context_id=c.id "
                        "JOIN amex_statement_imports i ON i.id=c.import_id "
                        "WHERE c.entity_code=:entity AND c.company_code=:company AND c.receiver_rfc=:rfc "
                        "AND i.card_account_id=ANY(CAST(:cards AS uuid[])) ORDER BY c.id"
                    ),
                    {
                        "entity": config["entity_code"],
                        "company": config["company_code"],
                        "rfc": config["receiver_rfc"],
                        "cards": _ids(config["card_account_ids"]),
                    },
                )
            )
            .mappings()
            .all()
        )
        accepted = {base_hash, full_hash}
        accepted.update(
            (
                await session.execute(
                    text(
                        f"SELECT config_hash FROM {TABLE} WHERE base_config_hash=:hash"
                    ),
                    {"hash": base_hash},
                )
            )
            .scalars()
            .all()
        )
        if (not anchors and not previous) or any(
            r["config_hash"] not in accepted for r in anchors
        ):
            raise DirectConflict("policy_existing_base_context_anchor_required")
        request = args.request_id + ":" + base_hash
        plans.append(
            {
                "id": str(uuid4()),
                "base_config_hash": base_hash,
                "config_hash": full_hash,
                "config_text": canonical(config),
                "base_config_text": canonical(base),
                "previous_policy_id": str(previous["id"]) if previous else None,
                "revision": int(previous["revision"]) + 1 if previous else 1,
                "request_id": request,
                "actor_id": str(args.actor_id),
                "reason": args.reason.strip(),
                "anchors": json.loads(canonical([dict(v) for v in anchors])),
                "catalog": await _catalog(session, config),
            }
        )
    manifest = {
        "schema_version": VERSION,
        "actor_id": str(args.actor_id),
        "reason": args.reason.strip(),
        "request_id": args.request_id,
        "guard_proof": guard,
        "policies": plans,
    }
    manifest = json.loads(canonical(manifest))
    manifest["preview_hash"] = digest(manifest)
    return cast(dict[str, Any], manifest)


def validate_manifest(
    manifest: dict[str, Any], args: argparse.Namespace, configs: list[dict[str, Any]]
) -> None:
    before = {k: v for k, v in manifest.items() if k != "preview_hash"}
    if (
        manifest.get("schema_version") != VERSION
        or manifest.get("actor_id") != str(args.actor_id)
        or manifest.get("reason") != args.reason.strip()
        or manifest.get("request_id") != args.request_id
        or manifest.get("preview_hash") != args.preview_hash
        or digest(before) != args.preview_hash
        or not isinstance(manifest.get("policies"), list)
        or len(manifest["policies"]) != len(configs)
    ):
        raise DirectConflict("policy_manifest_mismatch")
    by_base = {digest(base_context_config(c)): c for c in configs}
    if len({p["base_config_hash"] for p in manifest["policies"]}) != len(by_base):
        raise DirectConflict("policy_manifest_mismatch")
    for plan in manifest["policies"]:
        config = by_base.get(plan["base_config_hash"])
        if (
            not config
            or plan["config_text"] != canonical(config)
            or plan["base_config_text"] != canonical(base_context_config(config))
            or plan["config_hash"] != digest(config)
        ):
            raise DirectConflict("policy_configuration_changed")
        UUID(plan["id"])
        if (
            plan["actor_id"] != str(args.actor_id)
            or plan["reason"] != args.reason.strip()
            or plan["request_id"] != args.request_id + ":" + plan["base_config_hash"]
        ):
            raise DirectConflict("policy_manifest_mismatch")


async def apply_policy_manifest(
    session: Any,
    args: argparse.Namespace,
    manifest: dict[str, Any],
    configs: list[dict[str, Any]],
) -> dict[str, Any]:
    validate_manifest(manifest, args, configs)
    # Match application lock order: the audited actor precedes the policy owner.
    await _authorize(session, args, configs, locked=True)
    await session.execute(text(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE"))
    if await _guard_proof(session) != manifest["guard_proof"]:
        raise DirectConflict("policy_owner_guard_changed")
    existing = (
        (
            await session.execute(
                text(
                    f"SELECT * FROM {TABLE} WHERE request_id=ANY(CAST(:requests AS varchar[]))"
                ),
                {"requests": [p["request_id"] for p in manifest["policies"]]},
            )
        )
        .mappings()
        .all()
    )
    if existing:
        by_request = {p["request_id"]: p for p in manifest["policies"]}
        if len(existing) != len(by_request) or any(
            str(r["id"]) != by_request[r["request_id"]]["id"]
            or r["payload_hash"] != args.preview_hash
            or r["config_text"] != by_request[r["request_id"]]["config_text"]
            or str(r["actor_id"]) != str(args.actor_id)
            for r in existing
        ):
            raise DirectConflict("policy_idempotency_conflict")
        return {
            "status": "exists",
            "policy_ids": [str(r["id"]) for r in existing],
            "preview_hash": args.preview_hash,
        }
    refreshed = await preview_policy_manifest(session, args, configs)
    for actual, reviewed in zip(refreshed["policies"], manifest["policies"]):
        # Preview IDs are intentionally private fresh UUIDs; all other values
        # must still equal the reviewed plan under the owner table lock.
        if {k: v for k, v in actual.items() if k != "id"} != {
            k: v for k, v in reviewed.items() if k != "id"
        }:
            raise DirectConflict("policy_preview_changed")
    await session.execute(text(f"DROP TRIGGER {GUARD} ON {TABLE}"))
    for plan in manifest["policies"]:
        await session.execute(
            text(
                f"INSERT INTO {TABLE}(id,config_hash,base_config_hash,config_text,base_config_text,revision,previous_policy_id,actor_id,reason,request_id,payload_hash) VALUES(:id,:config_hash,:base_config_hash,:config_text,:base_config_text,:revision,:previous_policy_id,:actor_id,:reason,:request_id,:payload_hash)"
            ),
            {
                **{
                    k: plan[k]
                    for k in (
                        "config_hash",
                        "base_config_hash",
                        "config_text",
                        "base_config_text",
                        "revision",
                        "reason",
                        "request_id",
                    )
                },
                "id": UUID(plan["id"]),
                "previous_policy_id": (
                    UUID(plan["previous_policy_id"])
                    if plan["previous_policy_id"]
                    else None
                ),
                "actor_id": args.actor_id,
                "payload_hash": args.preview_hash,
            },
        )
    await session.execute(text(CREATE_GUARD))
    if await _guard_proof(session) != manifest["guard_proof"]:
        raise DirectConflict("policy_owner_guard_not_restored")
    return {
        "status": "installed",
        "policy_ids": [p["id"] for p in manifest["policies"]],
        "preview_hash": args.preview_hash,
    }


def _read_manifest(path: str) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_uid != os.getuid()
            or info.st_size > 5 * 1024 * 1024
        ):
            raise DirectConflict("policy_private_manifest_required")
        value = json.load(stream)
        if not isinstance(value, dict):
            raise DirectConflict("policy_manifest_invalid")
        return cast(dict[str, Any], value)


def write_private_manifest(path: str, manifest: dict[str, Any]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)


async def run(args: argparse.Namespace, database_url: str) -> dict[str, Any]:
    if (
        not args.reason.strip()
        or len(args.reason.strip()) > 1000
        or not args.request_id.strip()
        or len(args.request_id) > 80
    ):
        raise DirectConflict("policy_explicit_reason_and_request_required")
    configs = configured_policies()
    manifest = None
    if args.command == "apply":
        if not all((args.confirmed, args.preview_hash, args.manifest)):
            raise DirectConflict("policy_confirmation_required")
        manifest = _read_manifest(args.manifest)
        validate_manifest(manifest, args, configs)
    elif not args.manifest_output:
        raise DirectConflict("policy_private_preview_output_required")
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(
            engine, expire_on_commit=False, autoflush=False
        ) as session:
            async with session.begin():
                if args.command == "preview":
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                await require_amex_direct_schema(session)
                if manifest is not None:
                    return await apply_policy_manifest(session, args, manifest, configs)
                preview = await preview_policy_manifest(session, args, configs)
                write_private_manifest(args.manifest_output, preview)
                return {
                    "status": "preview",
                    "preview_hash": preview["preview_hash"],
                    "policy_count": len(preview["policies"]),
                    "manifest_path": args.manifest_output,
                }
    finally:
        await engine.dispose()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("preview", "apply"), nargs="?", default="preview"
    )
    parser.add_argument("--actor-id", required=True, type=UUID)
    parser.add_argument("--reason", required=True)
    parser.add_argument(
        "--request-id", "--idempotency-key", required=True, dest="request_id"
    )
    parser.add_argument(
        "--database-url-env", default="AMEX_TREATMENT_POLICY_DATABASE_URL"
    )
    parser.add_argument("--manifest-output")
    parser.add_argument("--manifest")
    parser.add_argument("--preview-hash")
    parser.add_argument("--confirmed", action="store_true")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    database_url = os.environ.get(args.database_url_env)
    if not database_url:
        print(
            json.dumps(
                {"status": "blocked", "reason": "explicit_database_url_required"}
            )
        )
        return 1
    try:
        result = asyncio.run(run(args, database_url))
    except (DirectAccessError, DirectConflict) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}))
        return 1
    except SQLAlchemyError:
        print(
            json.dumps(
                {"status": "blocked", "reason": "policy_database_operation_failed"}
            )
        )
        return 1
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"status": "blocked", "reason": "policy_manifest_invalid"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
