import importlib.util
from pathlib import Path


def test_provisioning_script_requires_explicit_apply_flag():
    path = Path(__file__).resolve().parents[2] / "scripts" / "provision_client_executive_portfolio.py"
    spec = importlib.util.spec_from_file_location("provision_client_executive", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    assert module.main is not None
