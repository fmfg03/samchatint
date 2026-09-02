from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "backfill_document_payment_proofs.py"


def test_payment_proof_backfill_is_dry_run_by_default() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'parser.add_argument("--execute", action="store_true")' in source
    assert "if not args.execute:" in source
    assert "--actor-id is required with --execute." in source


def test_payment_proof_backfill_processes_unpaid_proof_candidates() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "lower(coalesce(a.categoria, '')) = 'comprobante_pago'" in source
    assert (
        "(d.estado IS DISTINCT FROM 'pagado' OR d.pagado_en IS NULL)"
        in source
    )
    assert "d.gasto_generado_id IS NULL" in source
    assert "notify=not args.suppress_notifications" in source
    assert "default=True" in source
