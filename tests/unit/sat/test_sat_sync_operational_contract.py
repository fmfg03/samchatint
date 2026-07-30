from pathlib import Path

from devnous.sat.sat_sync_service import SATSyncService


ROOT = Path(__file__).resolve().parents[3]


def read_repo_file(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def test_sat_sync_service_processes_received_and_issued_directions() -> None:
    assert SATSyncService.DIRECTIONS == ("received", "issued")


def test_full_sync_runner_documents_9am_and_11pm_schedule_without_secret() -> None:
    script = read_repo_file("scripts/run_sat_cfdi_sync.sh")

    assert "0 9,23 * * * /root/samchat/scripts/run_sat_cfdi_sync.sh" in script
    assert "/ingress/sat-cfdi-sync?mode=${MODE}" in script
    assert "X-SAT-Sync-Secret: ${SECRET}" in script
    assert "SAT_SYNC_SECRET is required" in script
    assert "sk-" not in script
    assert "BEGIN PRIVATE KEY" not in script


def test_open_jobs_runner_documents_hourly_schedule_without_secret() -> None:
    script = read_repo_file("scripts/run_sat_open_jobs.sh")

    assert "15 * * * * /root/samchat/scripts/run_sat_open_jobs.sh" in script
    assert "/ingress/sat-cfdi-open-jobs" in script
    assert "X-SAT-Sync-Secret: ${SECRET}" in script
    assert "SAT_SYNC_SECRET is required" in script
    assert "sk-" not in script
    assert "BEGIN PRIVATE KEY" not in script


def test_sat_ingress_docstrings_match_operational_schedule() -> None:
    source = read_repo_file("src/devnous/gastos/routes/webhook_handler.py")

    assert "daily 09:00 and 23:00" in source
    assert "0 9,23 * * * /root/samchat/scripts/run_sat_cfdi_sync.sh" in source
    assert "15 * * * * /root/samchat/scripts/run_sat_open_jobs.sh" in source


def test_admin_sat_console_surfaces_received_issued_and_runner_scripts() -> None:
    source = read_repo_file("src/devnous/gastos/routes/admin_routes.py")

    assert "09:00 y 23:00 CDMX" in source
    assert "scripts/run_sat_cfdi_sync.sh" in source
    assert "scripts/run_sat_open_jobs.sh" in source
    assert "Recibidos + emitidos" in source
    assert "/admin/gastos/cfdis/matching" in source
