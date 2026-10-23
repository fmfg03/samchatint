"""Explicit fixture-owner policy installation in isolated AMEX PostgreSQL only."""

import copy
import hashlib
import json
import os
from uuid import uuid4

GRANT_KEYS = (
    "benefit_budget_line_ids",
    "benefit_budget_pairs",
    "partner_debtor_account_ids",
)
POLICY_GUARD_DDL = (
    "CREATE TRIGGER ad_account_treatment_policy_dml "
    "BEFORE INSERT OR UPDATE OR DELETE "
    "ON amex_direct_account_treatment_policies FOR EACH ROW "
    "EXECUTE FUNCTION amex_direct_immutable()"
)


def canonical_config(value):
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def base_fixture_config(config):
    result = copy.deepcopy(config)
    for key in GRANT_KEYS:
        result.pop(key, None)
    for scope in result.get("actor_scopes", []):
        for key in GRANT_KEYS:
            scope.pop(key, None)
    return result


async def install_account_treatment_policy(pg, config=None, *, request_id=None):
    """Use owner DDL, never an application/GUC allowance, to install test grants."""
    assert pg.factory.kw["bind"].url.port == 55471
    assert pg.factory.kw["bind"].url.database == "devnous_test"
    assert pg.schema.startswith("direct_acceptance_")
    config = config or json.loads(os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"])[0]
    full = canonical_config(config)
    base = canonical_config(base_fixture_config(config))
    full_hash = hashlib.sha256(full.encode()).hexdigest()
    base_hash = hashlib.sha256(base.encode()).hexdigest()
    async with pg.conn.transaction():
        assert await pg.conn.fetchval(
            "SELECT id FROM empleados WHERE id=$1 AND activo IS TRUE "
            "AND rol IN ('finanzas','admin','superadmin','super_admin') FOR KEY SHARE",
            pg.actor.id,
        )
        await pg.conn.execute(
            "LOCK TABLE amex_direct_account_treatment_policies "
            "IN ACCESS EXCLUSIVE MODE"
        )
        prior = await pg.conn.fetchrow(
            "SELECT * FROM amex_direct_account_treatment_policies "
            "WHERE base_config_hash=$1 ORDER BY revision DESC LIMIT 1",
            base_hash,
        )
        if prior and prior["config_hash"] == full_hash:
            return prior["id"]
        identity = uuid4()
        request = request_id or "fixture-policy-" + uuid4().hex
        reason = "Explicit isolated fixture owner policy installation"
        payload_hash = hashlib.sha256(
            canonical_config(
                {
                    "config_hash": full_hash,
                    "base_config_hash": base_hash,
                    "actor_id": pg.actor.id,
                    "request_id": request,
                    "reason": reason,
                }
            ).encode()
        ).hexdigest()
        await pg.conn.execute(
            "DROP TRIGGER ad_account_treatment_policy_dml "
            "ON amex_direct_account_treatment_policies"
        )
        await pg.conn.execute(
            "INSERT INTO amex_direct_account_treatment_policies "
            "(id,config_hash,base_config_hash,config_text,base_config_text,"
            "revision,previous_policy_id,actor_id,reason,request_id,payload_hash) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            identity,
            full_hash,
            base_hash,
            full,
            base,
            prior["revision"] + 1 if prior else 1,
            prior["id"] if prior else None,
            pg.actor.id,
            reason,
            request,
            payload_hash,
        )
        await pg.conn.execute(POLICY_GUARD_DDL)
        return identity
