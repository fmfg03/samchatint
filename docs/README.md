# SamChat documentation map

This index distinguishes current repository references from historical evidence and planning material. It does not prove production deployment or customer acceptance.

## Start here

- [Product canon](../SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md)
- [Engineering canon](../SAMCHAT_ENGINEERING_CANON_FOR_CHATGPT_2026-09-10.md)
- [Codebase sweep](../SAMCHAT_CODEBASE_SWEEP_REPORT_2026-09-10.md)
- [Runtime map](runtime_map.md)
- [Installation matrix](install_matrix.md)
- [Current release drop-in reference](release/current-release-dropin.md)

## Domain references

- `assistant/`, `product/`, `operations/`, `api/`, and `security/` contain domain material; verify evidence labels before relying on a claim.
- `roadmap/` and `sprints/` are planning records, not commitments or acceptance evidence.
- `release/` records release evidence and must be read with its stated date and commit.
- `artifacts/` and dated offer materials preserve historical evidence; they are not current runtime truth.

## Documentation audit

Run this read-only check from the repository root:

```bash
python scripts/audit_documentation.py --check
```

The inventory is review evidence only; it never authorizes a rewrite, production claim, or deletion of historical material.
