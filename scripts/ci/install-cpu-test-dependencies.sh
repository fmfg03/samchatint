#!/usr/bin/env bash
# Install the complete requested requirements, with CPU-only PyTorch for CI.
set -euo pipefail
python_bin=${1:?Python executable required}
shift
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
constraints="$root/.github/constraints-ci-cpu.txt"

disk_report() {
  echo "=== CI dependency disk usage: $1 ==="
  df -Pk "$root" "${TMPDIR:-/tmp}"
}
disk_report before
# Report even on install failure, without converting it to a successful job.
trap 'status=$?; disk_report after; exit "$status"' EXIT

# Fetch torch only from the official CPU index. The exact +cpu constraint below
# prevents the general resolver from replacing it with a CUDA build later.
"$python_bin" -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
  --constraint "$constraints" 'torch==2.15.0+cpu'
"$python_bin" -m pip install --no-cache-dir --constraint "$constraints" "$@"
"$python_bin" -m pip check
"$python_bin" - <<'PY'
from importlib.metadata import distributions, version
import torch
import transformers
from sentence_transformers import SentenceTransformer

assert version("torch") == "2.15.0+cpu", version("torch")
assert torch.version.cuda is None, torch.version.cuda
gpu_packages = sorted(
    d.metadata["Name"] for d in distributions()
    if d.metadata["Name"].lower().startswith("nvidia-")
    or d.metadata["Name"].lower() == "triton"
)
assert not gpu_packages, gpu_packages
for name in ("torch", "sentence-transformers", "transformers"):
    print(f"CI dependency: {name}=={version(name)}")
print(f"PyTorch CPU smoke: {torch.ones(2).sum().item()}")
PY
