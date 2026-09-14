# Documentation audit artifacts

`documentation-inventory-2026-09-14.json` is the deterministic inventory of tracked Markdown at the documented baseline. It records classifications, dispositions, link findings, and the owner for items requiring human review.

Run `python scripts/audit_documentation.py --output docs/audits/documentation-inventory-2026-09-14.json` after a reviewed documentation change to refresh it. `documentation-known-findings.json` is the reviewed baseline; `--check` fails only for new findings. The output is evidence for review; it does not authorize deletion or a production claim.
