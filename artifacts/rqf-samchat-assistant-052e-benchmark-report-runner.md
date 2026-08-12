# RQF-ASSISTANT-052E ? Specialist Benchmark Report Runner

Status: IMPLEMENTED_LOCAL

## Scope

This slice adds a formal report layer for the RQF-052 specialist benchmark
harness. It converts the deterministic seed benchmark suite into a durable
regression artifact rather than relying only on pytest output.

## Added

- `BenchmarkReport` summary model.
- Gap classification for:
  - missing evidence;
  - unsupported claims;
  - failed criteria.
- Compact JSON report output.
- Human-readable Markdown report output.
- CLI runner: `scripts/run_assistant_specialist_benchmarks.py`.
- Assistant scoped gate coverage for the report tests.

## Safety properties

- Report status is PASS only when every benchmark and criterion passes.
- Side effects are counted and surfaced.
- Known missing evidence remains visible as a gap without being converted into
  invented facts.
- Unsupported verifier claims and failed criteria are explicit report gaps.
- The CLI exits non-zero if the benchmark report is not PASS or side effects are
  detected.

## Verification

```bash
PYTHONPATH=src python3 -m pytest   tests/unit/test_assistant_specialist_contract.py   tests/unit/test_assistant_specialist_agents.py   tests/unit/test_assistant_specialist_benchmarks.py   tests/unit/test_assistant_specialist_report.py -q
# 27 passed

python3 scripts/run_assistant_specialist_benchmarks.py --format json --compact
python3 scripts/run_assistant_specialist_benchmarks.py --format markdown
```
