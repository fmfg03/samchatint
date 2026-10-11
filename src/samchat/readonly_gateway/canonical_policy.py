"""Pure canonical route predicate copied exactly from base 2cb5397.

Source: devnous/gastos/services/project_authorization_service.py.
AST equivalence is checked offline; no ORM/application imports or writes.
This is route authority, not a universal document visibility or approval gate.
"""


def document_route_approver_sql(
    document_alias: str = "documentos", actor_parameter: str = "route_employee_id"
) -> str:
    """Shared read-only SQL policy; arguments are trusted query identifiers."""
    if not document_alias.isidentifier() or not actor_parameter.isidentifier():
        raise ValueError("Invalid query identifier")
    reference = (
        f"NULLIF(BTRIM({document_alias}.referencia_operaciones), '') IS NOT NULL"
    )
    valid = (
        "route.eligible_position_keys = '[\"director_operaciones\"]'::jsonb "
        "AND route.requires_operations_reference = TRUE"
    )
    snapshot = f"""EXISTS (
        SELECT 1 FROM documento_authorization_routes route
        WHERE route.documento_id = {document_alias}.id AND ({valid})
          AND :{actor_parameter} IN (
              SELECT jsonb_array_elements_text(route.eligible_empleado_ids))
    )"""
    fallback = f"""NOT EXISTS (
        SELECT 1 FROM documento_authorization_routes route
        WHERE route.documento_id = {document_alias}.id AND ({valid})
    ) AND EXISTS (
        SELECT 1 FROM authorization_position_assignments a
        JOIN authorization_positions p
          ON p.position_key = a.position_key AND p.active = TRUE
        JOIN empleados operations_holder
          ON operations_holder.id = a.empleado_id
         AND operations_holder.activo = TRUE
        WHERE a.active = TRUE AND a.position_key = 'director_operaciones'
          AND a.empleado_id::text = :{actor_parameter}
    )"""
    natural = f"""EXISTS (
        SELECT 1 FROM documento_authorization_routes route
        WHERE route.documento_id = {document_alias}.id
          AND :{actor_parameter} IN (
              SELECT jsonb_array_elements_text(route.eligible_empleado_ids))
    )"""
    return f"""(
        (({reference}) AND (({snapshot}) OR ({fallback})))
        OR (NOT ({reference}) AND ({natural}))
    ) AND EXISTS (
        SELECT 1 FROM empleados active_actor
        WHERE active_actor.id::text = :{actor_parameter}
          AND active_actor.activo = TRUE
    )"""
