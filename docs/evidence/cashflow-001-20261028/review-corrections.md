# PR513 review corrections

Three verified review findings are addressed: budget-edition filter context, JSON/JSONB serialization of exact and unavailable amounts, and authorized project visibility independent of pending AP. The additional source file finance_read_adapter.py is the effective T04 consumer registered before editing; only the cashflow JSON boundary changes. Internal Decimal arithmetic and all financial owner/permission/schema/dependency rules remain unchanged.

248acceptancepassed (4isolated PostgreSQL,2browser),91.68%measuredcoverage including adapter;135private-pluginpassed; inventory613/578/44/no gaps. Scoped mypy passes4modules. Independent read-only review reproduced and closed the nonfinite JSON edge, with no remaining findings. Existing AR/budget/source helper AST and existing adapter tests remain intact. Global lint/type debt is not claimed clean.

Added regressions submit the actual year/edition form values, test AR/budget-only and tenant/tournament-limited project choices, serialize actual cashflow DTOs through strictjson and AssistantRun JSONB bind, and reject nonfinite JSON tokens while preserving source Decimal.

Oldhead0710 CI passed6686unit/840integration/differentialcoverage91%, but newhead must rerun fullCI before merge. Review threads must be resolved only after publishing these fixes. The release must apply only the JSON hunks to its existing adapter, preserving live AR behavior. Canon unchanged. Authenticated Finance UAT remains pending.
