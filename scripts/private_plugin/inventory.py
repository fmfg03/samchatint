"""Static inventory: reads versioned Python only, never imports the application."""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/private-plugin"
BASELINE = "6cd122f1030ab39631d77e706a9f5a1a7192231d"
METHODS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "websocket",
    "api_route",
}
# Discovery is deliberately limited to tracked Python in the canonical tree.
# Never traverse uploads, local credentials, environment files or nested apps.


def tracked_sources():
    names = subprocess.check_output(
        ["git", "ls-files", "--", "*.py"], cwd=ROOT, text=True
    ).splitlines()
    return [
        ROOT / name
        for name in names
        if ("/" not in name or name.startswith("src/"))
        and not name.startswith("src/samchat/private_plugin/")
    ]


def module_file(module):
    path = ROOT / "src" / module.replace(".", "/")
    for candidate in (path.with_suffix(".py"), path / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def imported_symbol(file, symbol, visited=()):
    """Resolve source imports only; conditional imports remain static evidence."""
    key = (str(file), symbol)
    if key in visited:
        return None
    tree = ast.parse(file.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if (alias.asname or alias.name) != symbol:
                continue
            if node.level:
                base = file.parent
                for _ in range(node.level - 1):
                    base = base.parent
                target = base.joinpath(*(node.module or "").split("."))
                target = (
                    target.with_suffix(".py")
                    if target.with_suffix(".py").is_file()
                    else target / "__init__.py"
                )
            else:
                target = module_file(node.module or "")
            if target and target.is_file():
                deeper = imported_symbol(target, alias.name, (*visited, key))
                return deeper or (target, alias.name)
    return None


def mounted_routers():
    """Resolve live entrypoint includes, retaining line evidence and conditions."""
    main = ROOT / "copa_telmex_dashboard.py"
    result = {
        (main.name, "app"): [
            {"prefix": "", "evidence": main.name, "status": "entrypoint_static"}
        ]
    }
    tree = ast.parse(main.read_text())
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and expr(node.func) == "app.include_router"
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            continue
        target = imported_symbol(main, node.args[0].id)
        if not target:
            continue
        prefix = next(
            (
                kw.value.value
                for kw in node.keywords
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant)
            ),
            "",
        )
        key = (str(target[0].relative_to(ROOT)), target[1])
        result.setdefault(key, []).append(
            {
                "prefix": prefix,
                "evidence": f"{main.name}:{node.lineno}",
                "status": (
                    "include_router_static; conditional/runtime execution unverified"
                ),
            }
        )
    # This module defines routes inside a registration function, not APIRouter.
    admin = ROOT / "src/devnous/gastos/routes/admin_routes.py"
    for node in ast.walk(ast.parse(admin.read_text())):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "register_presupuestos_routes"
            and node.args
            and expr(node.args[0]) == "router"
        ):
            target = imported_symbol(admin, node.func.id)
            if target:
                result[(str(target[0].relative_to(ROOT)), "router")] = [
                    {
                        "prefix": "",
                        "evidence": f"{admin.relative_to(ROOT)}:{node.lineno}",
                        "status": (
                            "registration_function_static; runtime execution unverified"
                        ),
                    }
                ]
    return result


def expr(node):
    return ast.unparse(node)


def family(path):
    for key, terms in [
        (
            "registration",
            [
                "registration-review",
                "players",
                "teams",
                "/player/",
                "/team/",
                "jugadores",
                "equipos",
                "ocr",
            ],
        ),
        ("budgets", ["presupuesto", "budget"]),
        ("receivables", ["cuentas-por-cobrar", "/ar/", "cobranza"]),
        ("payments", ["payment-run", "pago", "payment", "reembolso"]),
        ("accounting", ["coi", "contab", "diot", "amex", "nomina", "cfdi", "/sat"]),
        ("direction", ["/direccion", "/client"]),
        ("assistant", ["assistant"]),
        ("identity", ["login", "logout", "auth", "perfil", "empleado", "permission"]),
        ("support", ["support", "soporte"]),
        ("operations", ["operacion", "torneo", "tournament", "soul", "media"]),
        ("finance", ["finanza", "cashflow", "bank", "banco"]),
        ("artifacts", ["artifact", "inbox"]),
    ]:
        if any(term in path.lower() for term in terms):
            return key
    return "documents_and_other"


def access_profile_catalog():
    """Extract default policy evidence without evaluating/importing service code."""
    file = ROOT / "src/devnous/gastos/services/access_control_service.py"
    tree = ast.parse(file.read_text())
    constants = {}

    def literal(node):
        if isinstance(node, ast.Name):
            return constants[node.id]
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return [literal(n) for n in node.elts]
        if isinstance(node, ast.Call) and expr(node.func) == "frozenset":
            return literal(node.args[0]) if node.args else []
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return literal(node.left) + literal(node.right)
        raise ValueError("non-literal policy")

    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            try:
                constants[node.targets[0].id] = literal(node.value)
            except (KeyError, ValueError, TypeError):
                pass
    rows = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and expr(node.func) == "AccessTool"):
            continue
        try:
            args = [literal(n) for n in node.args]
            row = dict(
                zip(
                    (
                        "key",
                        "label",
                        "group",
                        "description",
                        "paths",
                        "default_roles",
                        "actions",
                    ),
                    args,
                )
            )
            row.update({kw.arg: literal(kw.value) for kw in node.keywords})
            row.setdefault("actions", ["ver"])
            row["evidence"] = f"{file.relative_to(ROOT)}:{node.lineno}"
            row["status"] = (
                "DEFAULT_POLICY_ONLY; configured denials, active positions and object"
                " scope prevail"
            )
            rows.append(row)
        except (KeyError, ValueError, TypeError):
            rows.append(
                {
                    "evidence": f"{file.relative_to(ROOT)}:{node.lineno}",
                    "status": "DYNAMIC_POLICY_UNRESOLVED",
                }
            )
    return rows


def scan():
    rows, errors, registrations = [], [], []
    files = sorted(tracked_sources())
    mounts = mounted_routers()
    access_tools = access_profile_catalog()
    for file in files:
        rel = str(file.relative_to(ROOT))
        # Tracked source allowlist: never inspect env, uploads, or credentials.
        source = file.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            errors.append(
                {"source": rel, "line": exc.lineno, "gap": "AST_PARSE_FAILED"}
            )
            continue
        prefixes = {}
        guard_definitions = {
            n.name: n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if isinstance(value, ast.Call) and expr(value.func).endswith(
                    "APIRouter"
                ):
                    names = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    for name in names:
                        prefixes[expr(name)] = next(
                            (
                                kw.value.value
                                for kw in value.keywords
                                if kw.arg == "prefix"
                                and isinstance(kw.value, ast.Constant)
                            ),
                            "",
                        )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"include_router", "add_api_route", "mount"}
            ):
                registrations.append(
                    {
                        "source": rel,
                        "line": node.lineno,
                        "expression": expr(node),
                        "gap": "STATIC_REGISTRATION_REVIEW",
                    }
                )
        for fun in ast.walk(tree):
            if not isinstance(fun, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in fun.decorator_list:
                if not (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in METHODS
                    and dec.args
                ):
                    continue
                raw_path = dec.args[0]
                path = (
                    raw_path.value
                    if isinstance(raw_path, ast.Constant)
                    and isinstance(raw_path.value, str)
                    else "DYNAMIC:" + expr(raw_path)
                )
                method = dec.func.attr.upper()
                calls = sorted(
                    {expr(n.func) for n in ast.walk(fun) if isinstance(n, ast.Call)}
                )
                guards = sorted(
                    {
                        expr(n)
                        for n in ast.walk(fun)
                        if isinstance(n, ast.Call)
                        and any(
                            s in expr(n.func).lower()
                            for s in [
                                "require",
                                "ensure",
                                "permission",
                                "can_access",
                                "authorize",
                                "current_empleado",
                                "allowed",
                                "scope",
                            ]
                        )
                    }
                )
                domains = [
                    c
                    for c in calls
                    if any(
                        s in c.lower()
                        for s in [
                            "service",
                            "adapter",
                            "commit",
                            "approve",
                            "reject",
                            "send_document",
                            "execute_canonical",
                            "build_",
                            "validate",
                        ]
                    )
                ]
                router_name = expr(dec.func.value)
                prefix = prefixes.get(router_name, "")
                mount_records = mounts.get((rel, router_name), [])
                paths = [m["prefix"] + prefix + path for m in mount_records]
                declared_path = prefix + path
                # api_route may expose multiple methods; retain all without guessing.
                declared_methods = [method]
                if method == "API_ROUTE":
                    method_kw = next(
                        (kw.value for kw in dec.keywords if kw.arg == "methods"), None
                    )
                    declared_methods = (
                        ast.literal_eval(method_kw)
                        if isinstance(method_kw, (ast.List, ast.Tuple))
                        else (
                            ["GET"]
                            if method_kw is None
                            else ["DYNAMIC_METHODS_UNPROVEN"]
                        )
                    )
                profile_refs = []
                role_literals = set()
                for call in ast.walk(fun):
                    if not isinstance(call, ast.Call):
                        continue
                    name = expr(call.func)
                    if any(
                        t in name.lower()
                        for t in (
                            "require",
                            "permission",
                            "authorize",
                            "current_empleado",
                            "can_",
                            "scope",
                        )
                    ):
                        definition = guard_definitions.get(name)
                        if definition:
                            profile_refs.append(f"{rel}:{definition.lineno}:{name}")
                            for item in ast.walk(definition):
                                if isinstance(item, ast.Attribute) and (
                                    "Rol" in expr(item.value)
                                    or "Permission" in expr(item.value)
                                ):
                                    role_literals.add(expr(item))
                for item in ast.walk(fun):
                    if isinstance(item, ast.Attribute) and (
                        "Rol" in expr(item.value) or "Permission" in expr(item.value)
                    ):
                        role_literals.add(expr(item))
                lane = family(prefix + path)
                is_read = all(m in {"GET", "HEAD", "OPTIONS"} for m in declared_methods)
                uid = hashlib.sha256(
                    f"{rel}:{fun.lineno}:{method}:{path}".encode()
                ).hexdigest()[:12]
                rows.append(
                    {
                        "id": uid,
                        "source": rel,
                        "line": fun.lineno,
                        "handler": fun.name,
                        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                        "method": method,
                        "paths": paths,
                        "declared_path": declared_path,
                        "declared_methods": declared_methods,
                        "router": router_name,
                        "mount_chain": mount_records,
                        "guard_definitions": sorted(set(profile_refs)),
                        "profile_symbols": sorted(role_literals),
                        "default_profile_candidates": [
                            {
                                "key": t["key"],
                                "roles": t["default_roles"],
                                "actions": t["actions"],
                                "evidence": t["evidence"],
                            }
                            for t in access_tools
                            if "paths" in t
                            and any(
                                p == root or p.startswith(root.rstrip("/") + "/")
                                for p in paths
                                for root in t["paths"]
                            )
                        ],
                        "profile_mapping_status": (
                            "UNPROVEN: guards/dependencies are evidence, not a complete"
                            " effective-role matrix"
                        ),
                        "family": lane,
                        "mount_evidence": (
                            "live_entrypoint_static"
                            if mount_records
                            else "secondary_or_unproven_mount"
                        ),
                        "profile_evidence": (
                            guards
                            or [
                                "NO_LOCAL_GUARD_FOUND; inherited/middleware review"
                                " required"
                            ]
                        ),
                        "service_candidates": domains,
                        "canonical_owner": (
                            "ROUTE OWNER ONLY; service authority unproven:"
                            f" {rel}:{fun.name}"
                        ),
                        "proposed_tool": (
                            f"{lane}_{fun.name}_{method.lower()}"[:56] + "_" + uid[:6]
                        ),
                        "scope_proposal": (
                            f'{lane}:{"read" if is_read else "write"}; never grants'
                            " business authority"
                        ),
                        "object_parameters": [
                            arg.arg
                            for arg in fun.args.args
                            if arg.arg not in {"request", "session", "db"}
                        ],
                        "business_scope": (
                            "current employee + effective faculty + object +"
                            " organization/legal entity + portfolio/tournament;"
                            " UNPROVEN"
                        ),
                        "effect": (
                            "read_candidate; side effects NOT verified"
                            if is_read
                            else "write_or_effect_candidate"
                        ),
                        "confirmation": (
                            "none only after read-purity proof"
                            if is_read
                            else (
                                "trusted human approval of exact"
                                " draft/version/amount/currency/destination/digest;"
                                " recheck at commit"
                            )
                        ),
                        "evidence_idempotency": (
                            "scoped source IDs/version and redacted durable receipt;"
                            " writes need canonical transactional dedup + postcondition"
                        ),
                        "tests_required": (
                            [
                                "role/object/company deny",
                                "revoked/expired",
                                "isolation",
                                "read purity",
                            ]
                            + (
                                []
                                if is_read
                                else [
                                    "exact confirmation/version",
                                    "retry/crash/no duplicates",
                                    "verified receipt",
                                ]
                            )
                        ),
                        "status": (
                            "DISABLED; CANONICAL_SCOPE_UNPROVEN; route presence is not"
                            " adapter or UI parity proof"
                        ),
                    }
                )
    action_file = ROOT / "src/samchat/assistant/action_router.py"
    actions = []
    adapter_file = ROOT / "src/samchat/assistant/adapters.py"
    adapter_tree = ast.parse(adapter_file.read_text())
    definitions = {
        n.name: n
        for n in ast.walk(adapter_tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    action_tree = ast.parse(action_file.read_text())
    read_actions = set()
    for node in ast.walk(action_tree):
        if isinstance(node, ast.FunctionDef) and node.name == "supported_read_actions":
            read_actions.update(
                n.value
                for n in ast.walk(node)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            )
    for n in ast.walk(ast.parse(action_file.read_text())):
        if (
            isinstance(n, ast.AnnAssign)
            and isinstance(n.target, ast.Name)
            and n.target.id == "_ROUTES"
        ):
            for key, val in zip(n.value.keys, n.value.values):
                definition = definitions.get(expr(val))
                service_calls = (
                    sorted(
                        {
                            expr(c.func)
                            for c in ast.walk(definition)
                            if isinstance(c, ast.Call)
                        }
                    )
                    if definition
                    else []
                )
                action_read = key.value in read_actions
                actions.append(
                    {
                        "action": key.value,
                        "adapter": expr(val),
                        "adapter_source": (
                            f"src/samchat/assistant/adapters.py:{definition.lineno}"
                            if definition
                            else "UNRESOLVED"
                        ),
                        "service_candidates": service_calls,
                        "transitive_local_helpers": [
                            {
                                "source": (
                                    "src/samchat/assistant/adapters.py:"
                                    f"{definitions[name].lineno}"
                                ),
                                "name": name,
                                "calls": sorted(
                                    {
                                        expr(c.func)
                                        for c in ast.walk(definitions[name])
                                        if isinstance(c, ast.Call)
                                    }
                                ),
                            }
                            for name in service_calls
                            if name in definitions
                        ],
                        "proposed_tool": key.value.replace(".", "_"),
                        "effect": (
                            "declared_read; purity unproven"
                            if action_read
                            else "declared_write"
                        ),
                        "scope_proposal": (
                            key.value
                            + "; current server-resolved authority and object scope"
                            " required"
                        ),
                        "profile_evidence": (
                            "AssistantContext is not authorization; effective employee"
                            " role, denials, entity, portfolio, tournament and object"
                            " checks unproven"
                        ),
                        "confirmation": (
                            "none after purity proof"
                            if action_read
                            else (
                                "exact draft/version/amount/currency/destination;"
                                " recheck authority at commit"
                            )
                        ),
                        "evidence_idempotency": (
                            "canonical postcondition + durable redacted receipt;"
                            " transactional dedup proof required for writes"
                        ),
                        "tests_required": (
                            [
                                "role/company/object deny",
                                "expired/revoked",
                                "isolation",
                                "read purity",
                            ]
                            + (
                                []
                                if action_read
                                else [
                                    "confirmation/version binding",
                                    "retry/no duplicate",
                                    "durable receipt",
                                ]
                            )
                        ),
                        "source": "src/samchat/assistant/action_router.py",
                        "line": key.lineno,
                        "status": (
                            "DISABLED;"
                            " EXISTING_CANONICAL_CANDIDATE_NOT_PLUGIN_AUTHORITY"
                        ),
                    }
                )
    return {
        "baseline": BASELINE,
        "source_file_count": len(files),
        "exclusions": [
            (
                "Private plugin integration package excluded from business-source"
                " inventory; baseline pinned deliberately, source hashes detect changes"
            ),
            (
                "Active frontend is not versioned here; UI function/profile parity"
                " remains UNPROVEN"
            ),
            (
                "Nested applications, non-Python routes, generated/dynamic dispatch and"
                " external integrations are not exhaustive in this AST scan"
            ),
            (
                "Route declarations outside resolved live mounts retained with"
                " paths=[]; declared_path is not a real mounted URL"
            ),
            (
                "add_api_route/mount/include_router registrations retained separately;"
                " dynamic registrations require review"
            ),
            "HTTP method does not establish read purity; even GET may mutate",
            (
                "Local guard calls/symbols do not prove transitive authority,"
                " organization or object isolation"
            ),
            "No production runtime, UI, personal data or credentials inspected",
        ],
        "method": "AST static route declarations, not runtime import or frontend proof",
        "routes": rows,
        "actions": actions,
        "access_profile_catalog": access_tools,
        "registrations": registrations,
        "parse_gaps": errors,
    }


def render(data):
    columns = [
        "id",
        "source",
        "line",
        "handler",
        "method",
        "paths",
        "declared_path",
        "declared_methods",
        "mount_chain",
        "guard_definitions",
        "profile_symbols",
        "default_profile_candidates",
        "profile_mapping_status",
        "family",
        "mount_evidence",
        "profile_evidence",
        "canonical_owner",
        "service_candidates",
        "proposed_tool",
        "scope_proposal",
        "object_parameters",
        "business_scope",
        "effect",
        "confirmation",
        "evidence_idempotency",
        "tests_required",
        "status",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in data["routes"]:
        writer.writerow(
            {
                k: (
                    json.dumps(row[k], ensure_ascii=False)
                    if isinstance(row[k], list)
                    else row[k]
                )
                for k in columns
            }
        )
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n", buf.getvalue()


if __name__ == "__main__":
    data = scan()
    json_text, csv_text = render(data)
    OUT.mkdir(exist_ok=True)
    (OUT / "route-inventory.json").write_text(json_text)
    (OUT / "route-matrix.csv").write_text(csv_text)
    print(
        json.dumps(
            {
                "routes": len(data["routes"]),
                "live_static": sum(
                    r["mount_evidence"] == "live_entrypoint_static"
                    for r in data["routes"]
                ),
                "canonical_actions": len(data["actions"]),
                "parse_gaps": data["parse_gaps"],
            }
        )
    )
