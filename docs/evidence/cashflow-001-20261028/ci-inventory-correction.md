# CI inventory correction

PR513 job123015223666 failed reproducibility because inventory was generated while service.py still had three index stages from the integration merge. git ls-files emitted the same source path three times; count487 differed from clean checkout485. The resolved working-tree code was identical.

Regeneration after staging/committing the resolved merge changes only source_file_count from487to485; matrix CSV, routes, fingerprints and every other JSON field are identical. Independent read-only comparison against a clean checkout confirmed this. No scanner or runtime source change. All seven final runtime SHA256 hashes still match publication-integration.json.

135 private-plugin tests passed again against the corrected artifact. Classification: caused by task generation timing, corrected. Canon unchanged. Full GitHub suites must rerun before merge.
