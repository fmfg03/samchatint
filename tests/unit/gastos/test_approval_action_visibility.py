from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_pending_approval_actions_are_sticky_inside_table_scroller():
    source = (
        ROOT / "src" / "devnous" / "gastos" / "routes" / "user_routes.py"
    ).read_text(encoding="utf-8")

    start = source.index("async def documentos_pendientes(")
    end = source.index("def _documentos_todos_reporting_type", start)
    block = source[start:end]

    assert '<th class="approval-action-col">Acciones</th>' in block
    assert '<td class="approval-action-col">{actions_html}</td>' in block
    assert ".approval-action-col {" in block
    assert "position: sticky;" in block
    assert "right: 0;" in block
    assert "min-width: 190px;" in block
