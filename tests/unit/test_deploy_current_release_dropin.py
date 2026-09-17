from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_current_release_dropin.sh"


def test_deploy_current_release_preserves_or_fails_frontend_bundle() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "ensure_frontend_bundle" in script
    assert "goal-fest-page/dist" in script
    assert "index.html" in script
    assert "cp -a" in script
    assert "exit 68" in script
    assert "no reusable frontend bundle was found" in script


def test_deploy_current_release_reuses_runtime_recognized_telmex_bundle_sources() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "ensure_copa_telmex_bundle" in script
    assert "COPA_TELMEX_DIST_SOURCE" in script
    assert "COPA_TELMEX_DIST_DIR" in script
    assert '"$current_release"/copatelmex.backup-*/dist' in script


def test_deploy_current_release_keeps_health_and_ready_smoke() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "http://127.0.0.1:8000/healthz" in script
    assert "http://127.0.0.1:8000/readyz" in script
    assert "ln -sfnT \"$release\" /srv/samchat/current" in script
    assert "replaces the current symlink itself" in script
