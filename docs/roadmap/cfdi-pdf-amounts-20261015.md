# CFDI PDF amount extraction correction

Date: 2026-10-15. Candidate only; no commit, push, PR, merge or deployment.

## Scope and authority

Francisco approved implementation and tests for the two PDF readings and the
incorrect XML message. Base: `a983be79e1ad8a6b5aa56affe27a945160ac8166`.
Work branch: `fix/cfdi-pdf-amounts-20261015`, in an isolated worktree.
The previous checkout contained unrelated AMEX edits and was left untouched.

Runtime: `copa_telmex_dashboard.py`; domain: gastos quick expense intake.
Read owners: `cfdi_pdf_reader.py`, `cfdi_upload_resolver.py`, `cfdi_autofill.py`.
The existing quick-expense route remains the validation/write owner. This change
does not access Postgres/Supabase, migrate schemas, change accounting policy,
alter permissions, modify records or operate services. Repository evidence is
distinct from production and business acceptance; the base is not asserted to
be the deployed version.

Canon unchanged: this repairs documentary extraction and source-specific errors
under the existing missing-evidence and arithmetic-validation rules. The three
versioned canon Git blobs match the integrity register; checkout CRLF conversion
was distinguished from the canonical LF bytes. No canon or register was edited.

## Behavior

- A PDF content stream can place amounts before their labels. If required totals
  are unavailable, pypdf layout extraction associates labels with printed rows.
  It never sorts amounts or chooses their assignment by balancing an equation.
- Monetary tokens cannot stop at two decimals inside a four-decimal quantity,
  percentage, malformed grouped number or supported unit expression. A wrapped
  amount must occupy its own line. Discount summaries support ordinary, compact
  and CRLF layouts.
- Conflicting values under equivalent summary labels remain ambiguous. Generic
  Base and individual-tax labels remain lower-priority fallbacks.
- Absent PDF amounts remain `None`; subtotal, total and taxes are not invented
  by subtraction. Autocomplete leaves unavailable fields blank and preserves
  printed zero. Missing required totals or ambiguous amounts block submission,
  including shared-invoice submission.
- Optional absent discount/tax components retain the existing neutral arithmetic
  treatment after extraction. This is not documentary proof of a printed zero.
  The independently read fiscal total must still reconcile within the unchanged
  one-cent tolerance. No tax is fabricated to force that reconciliation.
- Source-specific errors identify PDF or XML in the main and supplement flows.
  Uploaded XML still takes precedence; embedded XML parsing is preserved.
  A printed zero total is not replaced by a computed nonzero value.

## Synthetic acceptance evidence

`tests/unit/gastos/test_cfdi_pdf_amounts.py` constructs anonymous PDFs in memory;
no original invoice or extracted private text is stored in the repository.

| Case | Before | Candidate |
| --- | --- | --- |
| Coffee, amounts before labels | subtotal 0, IVA 28, total 0 | subtotal 175, IVA 28, total 203 |
| Fuel, `Descuento` followed by `20.0000 LTR` | discount 20; computed total 539.80 | quantity rejected as discount; fiscal total 559.80 validates |
| Coffee with printed total 204 | unreliable extraction | printed 204 retained and arithmetic mismatch rejected |
| Missing/ambiguous totals | zero or balancing fallback | explicit missing/ambiguous blocker |

The focused suite also covers explicit/nonzero discounts, quantity/rate false
positives, compact/CRLF formats, thousands separators, withholding, explicit
zero, conflicting/equal totals, external-text fallback, unavailable layout,
shared-invoice validation, direct/embedded XML, XML precedence, ISH and both
submit call sites. An existing ISH test was changed to require missing IVA to
stay missing instead of manufacturing it from the total difference.

The new route tests compile the actual pure production functions with their
original line numbers. They do not reproduce their logic or boot the application.
The route wiring check inspects the actual quick-expense handler AST.

Validation: 55 tests passed in the independent final run (13.45 seconds, exit 0),
using real dependencies, without dependency stubs. The implementation run also
passed the preceding 48-case suite without stubs. Independent review found and
verified fixes for CRLF, compact discounts and conflicting subtotal synonyms;
no important findings remain in the reviewed final diff. `git diff --check`
passed. Additional receipts are delivered alongside the patch.

## Reproduction

With project test dependencies installed in a local Python 3.12 environment:

```powershell
$env:PYTHONPATH = 'src'
$env:MINERU_ENABLED = '0'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests/unit/gastos/test_cfdi_pdf_amounts.py tests/unit/gastos/test_ish_cfdi_upload_paths.py -o addopts='' -q
```

The host used its existing bundled Python 3.12 and dependencies from the local
isolated `.venv/Lib/site-packages`. Initial attempts encountered a missing
requests dependency, intermittent executor disconnections, and a Windows native
access violation during imports. An HTTP-blocking diagnostic harness was used
temporarily; it is not the basis of the final passing-suite claim. Normal runs
with actual requests/urllib3 subsequently passed. Interrupted attempts are not
counted as successful tests.

## Limits

- The original private PDFs were not available in this worktree. Generated PDFs
  reproduce the reported stream order and quantity token, not every detail of
  those private documents. Original-document smoke validation remains pending.
- Unknown/ambiguous layouts fail closed; the change does not promise universal
  OCR or every possible invoice layout. Layout extraction requires pypdf support;
  failure leaves the missing amount visible.
- This is targeted parser/validation verification, not full-suite CI, database
  integration, authenticated browser UAT or production acceptance.
- Numeric coverage was not obtained: both native and Python-tracer coverage
  attempts stopped returning output and were interrupted. No coverage percentage
  or compliance with a changed-code coverage gate is claimed. A whole-route
  syntax-compilation attempt also stalled; the actual changed pure functions
  and quick-expense handler were parsed/executed by the passing tests.
- No production state, service, permission, canon or accounting policy changed.
