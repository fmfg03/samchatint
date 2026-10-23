"""Pure canonical route SQL copied from project_authorization_service.

Every copied function has an exact AST equivalence acceptance guard.
No application imports, connections, writes, or independent authority policy.
Owner-resolved visibility remains separate from route authority.
"""


def document_route_approver_sql(
    document_alias: str = "documentos",
    actor_parameter: str = "route_employee_id",
    *,
    include_supervisor: bool = True,
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
    legacy = f"""(
        (({reference}) AND (({snapshot}) OR ({fallback})))
        OR (NOT ({reference}) AND ({natural}))
    )"""
    active = (
        f"({provisional_policy_active_sql()}) AND "
        f"{document_alias}.tipo IN ('SOLICITUD','INFORME')"
    )
    beneficiary = effective_beneficiary_sql(document_alias)
    project = effective_tournament_sql(document_alias)
    exception = f"""EXISTS (
        SELECT 1 FROM authorization_position_assignments protected
        JOIN authorization_positions pp ON pp.position_key=protected.position_key
            AND pp.active=TRUE
        JOIN empleados protected_person ON protected_person.id=protected.empleado_id
            AND protected_person.activo=TRUE
        WHERE protected.position_key='aprobaciones_escalan_gn'
          AND protected.active=TRUE AND (
            protected.empleado_id={document_alias}.empleado_id
            OR protected.empleado_id=({beneficiary})))"""
    assigned = f"""EXISTS (
        SELECT 1 FROM authorization_position_assignments approver
        JOIN authorization_positions ap ON ap.position_key=approver.position_key
            AND ap.active=TRUE
        WHERE approver.active=TRUE AND approver.empleado_id::text=:{actor_parameter}
          AND (({exception}) AND approver.position_key='aprobador_federico_gn'
          OR NOT ({exception}) AND EXISTS (
            SELECT 1 FROM project_authorization_rules rule
            WHERE rule.tournament_id=({project}) AND rule.active=TRUE
              AND approver.position_key IN (
                  SELECT jsonb_array_elements_text(rule.eligible_position_keys)))))"""
    supervisor = (
        approval_supervisor_sql(actor_parameter) if include_supervisor else "FALSE"
    )
    return f"""(
        ({active} AND (({assigned}) OR ({supervisor})))
        OR (NOT ({active}) AND ({legacy}))
    ) AND EXISTS (SELECT 1 FROM empleados active_actor
        WHERE active_actor.id::text=:{actor_parameter} AND active_actor.activo = TRUE)"""


def provisional_policy_active_sql() -> str:
    """Activation is explicit configuration, never inferred from generic roles."""
    return (
        "EXISTS (SELECT 1 FROM authorization_positions policy_activation "
        "WHERE policy_activation.position_key = 'aprobaciones_escalan_gn' "
        "AND policy_activation.active = TRUE)"
    )


def effective_tournament_sql(document_alias: str) -> str:
    return (
        f"COALESCE({document_alias}.torneo_id, (SELECT cg.torneo_id "
        f"FROM cuentas_de_gastos cg WHERE cg.id={document_alias}.cuenta_gastos_id))"
    )


def effective_beneficiary_sql(document_alias: str) -> str:
    return f"""CASE WHEN {document_alias}.beneficiario_empleado_id IS NOT NULL
        THEN {document_alias}.beneficiario_empleado_id
        WHEN {document_alias}.beneficiario_proveedor_cliente_id IS NOT NULL THEN NULL
        WHEN {document_alias}.cuenta_gastos_id IS NOT NULL THEN (
            SELECT CASE WHEN cg.beneficiario_proveedor_cliente_id IS NOT NULL THEN NULL
                   ELSE COALESCE(cg.beneficiario_empleado_id,cg.empleado_id) END
            FROM cuentas_de_gastos cg WHERE cg.id={document_alias}.cuenta_gastos_id)
        ELSE {document_alias}.empleado_id END"""


def approval_supervisor_sql(actor_parameter: str = "route_employee_id") -> str:
    """Only explicitly assigned active GV supervision, never generic admin."""
    if not actor_parameter.isidentifier():
        raise ValueError("Invalid query identifier")
    return f"""EXISTS (
        SELECT 1 FROM authorization_position_assignments supervisor
        JOIN authorization_positions sp ON sp.position_key=supervisor.position_key
            AND sp.active=TRUE
        JOIN empleados supervisor_actor ON supervisor_actor.id=supervisor.empleado_id
            AND supervisor_actor.activo=TRUE
        WHERE supervisor.position_key='supervision_aprobaciones_federico_gv'
          AND supervisor.active=TRUE AND supervisor.empleado_id::text=:{actor_parameter}
          AND ({provisional_policy_active_sql()})
    )"""
