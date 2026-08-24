# RQF-053E - Owner Variable Q&A

Status: IMPLEMENTED_LOCAL
Branch: `codex/rqf-053e-owner-variable-qa`
Date: 2026-08-24

## Objective

Allow the Owner/Director-style assistant path to answer concrete Owner Pack variables with evidence, and to fail closed when SamChat does not have supported data.

Example questions:

- `Cuantos equipos reales tenemos por categoria?`
- `Que entidades tienen pagos pendientes?`
- `Que falta para la carpeta de Jalisco?`
- `Que evidencia hay de visitas?`

## Product contract

The assistant must:

- route canonical Owner Pack variable questions to deterministic read-only logic before provider fallback;
- answer with supported evidence when live evidence exists;
- say `No hay dato soportado` when the variable is recognized but evidence is missing;
- never invent people, phone numbers, dates, amounts, teams, categories, or visit evidence;
- preserve `writes_attempted = 0` and `provider_called = False` for deterministic Owner Variable Q&A.

## Implementation notes

- Expanded Owner Variable aliases for pending operator payments.
- Added direct factual question detection for `que`, so natural questions such as `Que entidades tienen pagos pendientes?` do not fall through to generic request intelligence.
- Kept readiness questions such as `Que falta para la carpeta de Jalisco?` on the Owner Pack readiness path, with entity extraction from `carpeta`.
- Updated missing-data executive copy to use the explicit `No hay dato soportado` wording.

## Verification

Focused tests:

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest   tests/unit/test_assistant_owner_variable_query.py   tests/unit/test_assistant_owner_variable_answer.py   tests/unit/test_assistant_request_router_integration.py -q
```

Expected local result: `39 passed`.

## Non-claims

- This slice does not create missing operations data.
- This slice does not enable writes or approval actions.
- This slice does not claim the Owner Pack is complete; it only makes variable questions answerable or safely missing.
