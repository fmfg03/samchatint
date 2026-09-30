# Executable evidence for #432 and #433

2026-09-30. Synthetic local fixtures; not production evidence or business UAT.

- `regression-tests.txt`: 197 passing unit/HTTP tests covering Direction access and source contracts, budget service and canonical exporters.
- `browser-tests.txt`: 2 passing Chromium scenarios for desktop/mobile context/filter navigation and signed scenario/download parity.
- `report-page-1.png`, `report-page-2.png`, `report-page-3.png`: rendered PDF inspected for legibility, wrapped tables, business labels, graph scales, explicit gaps and isolated hypothetical values.
- `focused-tests.txt`: 25 passing final analysis/export and canonical exporter tests.
- New `tests/unit/test_direction_analysis_reports.py`: deterministic Decimal arithmetic, rounding parity, incomplete evidence, unauthorized scope, signed analysis tampering, no mutation, human legacy reports, Excel blanks/formula injection/cache and PDF numeric parity.

Changed executable Python lines measured from coverage XML plus the base diff: 482/503 (95.8%), above the 85% gate. Additional complete-concept and legacy-presentation tests were subsequently added. CI remains authoritative for the complete baseline-gated unit/integration suite.

Reproduce the focused suite with `PYTHONPATH=src python -m pytest tests/unit/test_direction* tests/unit/test_client_executive* tests/unit/test_budget_service.py tests/unit/test_executive_exporter.py tests/unit/gastos/test_direction_navigation_discovery.py tests/unit/gastos/test_executive_export_route_contract.py --cov=src --cov-report=xml --cov-fail-under=0` and Chromium with `PYTHONPATH=src python -m pytest tests/browser/test_direction_home_browser.py` (using the installed browser path if needed).

The initial CI run exposed that the monolithic requirements profile omitted the PDF libraries present in runtime/test profiles. Both requirements entrypoints now declare ReportLab and PyMuPDF; PDF checks remain mandatory, with no skips. Final PDF pagination keeps recommendation and definition blocks together.
