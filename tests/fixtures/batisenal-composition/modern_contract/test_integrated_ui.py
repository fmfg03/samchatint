"""Presentation regressions only: no application boot, identities, DB or grants.

Compile the actual route renderer with supplied view data. This does not test
authorization or prove integration with a real browser or MCP client.
"""
import ast
import copy
from html import escape
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parent
BASE = ast.parse((ROOT / "baseline/src/devnous/gastos/routes/batisenal_pilot_routes.py").read_text(encoding="utf-8"))
TREE = ast.parse((ROOT.parent / "c/src/devnous/gastos/routes/batisenal_pilot_routes.py").read_text(encoding="utf-8"))
REVOKED = "Revocada; el agente ya no tiene acceso"
EXPIRED = "Vencida; reconectar no amplía el plazo"


def named(tree, name):
    return next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def empty_branch(tree):
    return next(n for n in ast.walk(named(tree, "case")) if isinstance(n, ast.If) and isinstance(n.test, ast.BoolOp) and isinstance(n.test.op, ast.And) and ast.unparse(n.test.values[-1]) == "not drafts")


def render(permission, *, drafts=None, owner=True, support=False):
    detail = dict(title="Caso sintético", description="Descripción sintética", attention={"message": "En espera"}, context={}, can_support=support, private_owner_view=owner, pilot_permission=permission, comments=[])

    async def case_data(pilot, request):
        return detail, drafts or []

    namespace = dict(case_data=case_data, pilot=None, gate=lambda request: None,
                     escape=escape, PREFIX="/soporte/batisenal/piloto",
                     hidden=lambda fields: "", html=lambda body, **kw: body,
                     Request=object, secrets=SimpleNamespace(token_urlsafe=lambda size: "presentation-only"))
    helper = copy.deepcopy(named(TREE, "private_empty_message"))
    route = copy.deepcopy(named(TREE, "case"))
    route.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[helper, route], type_ignores=[]))
    exec(compile(module, "actual-route-presentation", "exec"), namespace)
    # Both coroutines are pure renderers. No event loop, socketpair or I/O is
    # needed; any suspension would be an unexpected test harness dependency.
    coroutine = namespace["case"](SimpleNamespace(session={}))
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coroutine.close()
    raise AssertionError("Presentation unexpectedly suspended for I/O")


class PresentationRegression(unittest.TestCase):
    def test_revoked_does_not_claim_absence_or_deletion(self):
        body = render(REVOKED)
        self.assertIn("no se muestra porque esta conexión está revocada", body)
        self.assertIn("no indica que las propuestas se hayan eliminado", body)
        self.assertNotIn("Aún no hay una propuesta", body)

    def test_expired_is_distinct_from_revoked(self):
        body = render(EXPIRED)
        self.assertIn("esta conexión ha vencido", body)
        self.assertNotIn("esta conexión está revocada", body)
        self.assertNotIn("Aún no hay una propuesta", body)

    def test_active_without_drafts_preserves_original_message(self):
        self.assertIn("Aún no hay una propuesta privada", render("Vigente dentro de la ventana del piloto"))

    def test_never_authorized_preserves_original_message(self):
        self.assertIn("Aún no hay una propuesta privada", render("Aún no autorizada"))

    def test_no_private_view_has_no_empty_or_hidden_message(self):
        body = render(REVOKED, owner=False)
        self.assertNotIn("El contenido privado", body)
        self.assertNotIn("Aún no hay una propuesta", body)

    def test_visible_draft_rendering_is_unchanged_and_escaped(self):
        body = render("Vigente", drafts=[dict(source_kind="synthetic_stub", body="<script>synthetic</script>")])
        self.assertIn("&lt;script&gt;synthetic&lt;/script&gt;", body)
        self.assertNotIn("El contenido privado no se muestra", body)
        self.assertNotIn("Aún no hay una propuesta", body)

    def test_repeated_navigation_keeps_message(self):
        self.assertEqual(render(REVOKED), render(REVOKED))
        self.assertIn("esta conexión está revocada", render(REVOKED, owner=False, support=True))

    def test_only_helper_and_empty_message_changed(self):
        actual = copy.deepcopy(TREE)
        actual.body = [n for n in actual.body if not (isinstance(n, ast.FunctionDef) and n.name == "private_empty_message")]
        branch = empty_branch(actual)
        baseline_branch = empty_branch(BASE)
        self.assertEqual(ast.dump(branch.test), ast.dump(baseline_branch.test))
        branch.body = copy.deepcopy(baseline_branch.body)
        self.assertEqual(ast.dump(actual), ast.dump(BASE))


if __name__ == "__main__":
    unittest.main(verbosity=2)
