"""
Schema guard utilities for sam.chat runtime compatibility.

This module provides:
- idempotent runtime schema fixes (safe to run on every startup)
- health checks for required tables/columns/indexes
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True)
class RequiredColumn:
    table: str
    column: str


@dataclass(frozen=True)
class RequiredIndex:
    table: str
    index: str


REQUIRED_COLUMNS: Sequence[RequiredColumn] = (
    RequiredColumn("expense_reports", "origen"),
    RequiredColumn("expense_reports", "numero_factura"),
    RequiredColumn("expense_reports", "solicitud_documento_id"),
    RequiredColumn("expense_reports", "informe_documento_id"),
    RequiredColumn("expense_reports", "cuenta_contable_id"),
    RequiredColumn("expense_reports", "contra_cuenta_contable_id"),
    RequiredColumn("expense_reports", "cfdi_uuid_manual"),
    RequiredColumn("expense_reports", "cfdi_report_id"),
    RequiredColumn("expense_reports", "referencia_base"),
    RequiredColumn("expense_reports", "cuenta_gastos_id"),
    RequiredColumn("expense_reports", "pagado_con_amex_empresa"),
    RequiredColumn("expense_reports", "categorias"),
    RequiredColumn("expense_reports", "edicion"),
    RequiredColumn("expense_reports", "currency"),
    RequiredColumn("documentos", "proveedor_cliente_id"),
    RequiredColumn("copa_telmex_players", "roster_index"),
    RequiredColumn("copa_telmex_players", "photo_sha256"),
    RequiredColumn("copa_telmex_players", "photo_ahash"),
    RequiredColumn("copa_telmex_teams", "contact_email"),
    RequiredColumn("copa_telmex_teams", "tournament_slug"),
    RequiredColumn("assistant_conversations", "metadata"),
    RequiredColumn("assistant_conversations", "tournament_key"),
    RequiredColumn("assistant_conversations", "archived"),
    RequiredColumn("assistant_messages", "tool_payload"),
    RequiredColumn("assistant_runs", "tool_trace"),
    RequiredColumn("assistant_artifacts", "metadata"),
    RequiredColumn("analyst_cases", "case_id"),
    RequiredColumn("analyst_cases", "status"),
    RequiredColumn("analyst_cases", "suggested_routes"),
    RequiredColumn("analyst_case_versions", "case_id"),
    RequiredColumn("analyst_case_versions", "version_number"),
    RequiredColumn("analyst_case_versions", "changed_fields"),
    RequiredColumn("empleados", "password_hash"),
    RequiredColumn("empleados", "aprobador_id"),
    RequiredColumn("documentos", "beneficiario_empleado_id"),
    RequiredColumn("documentos", "fecha_pago"),
    RequiredColumn("documentos", "concepto_pago"),
    RequiredColumn("documentos", "numero_factura"),
    RequiredColumn("documentos", "referencia_pago"),
    RequiredColumn("documentos", "metodo_pago"),
    RequiredColumn("documentos", "gasto_generado_id"),
    RequiredColumn("documentos", "referencia_base"),
    RequiredColumn("documentos", "referencia_operaciones"),
    RequiredColumn("documentos", "cuenta_gastos_id"),
    RequiredColumn("documentos", "cfdi_uuid_manual"),
    RequiredColumn("documentos", "cfdi_report_id"),
    RequiredColumn("documentos", "fase"),
    RequiredColumn("documentos", "categorias"),
    RequiredColumn("documentos", "edicion"),
    RequiredColumn("documentos", "currency"),
    RequiredColumn("cuentas_de_gastos", "categorias"),
    RequiredColumn("cuentas_de_gastos", "edicion"),
    RequiredColumn("cuentas_de_gastos", "currency"),
    RequiredColumn("tournaments", "etapas"),
    RequiredColumn("tournaments", "categorias"),
    RequiredColumn("access_profiles", "id"),
    RequiredColumn("empleado_access_profiles", "id"),
    RequiredColumn("reconciliation_audit_logs", "action"),
    RequiredColumn("regulatory_sources", "source_key"),
    RequiredColumn("labor_rule_snapshots", "rule_key"),
    RequiredColumn("tax_tables_isr", "periodicity"),
    RequiredColumn("tax_tables_subsidio_empleo", "periodicity"),
    RequiredColumn("social_security_tables", "component_key"),
    RequiredColumn("payroll_employers", "employer_key"),
    RequiredColumn("payroll_employer_registrations", "registration_code"),
    RequiredColumn("payroll_employees", "empleado_id"),
    RequiredColumn("payroll_periods", "period_type"),
    RequiredColumn("payroll_incidents", "incident_type"),
    RequiredColumn("payroll_runs", "run_type"),
    RequiredColumn("payroll_run_lines", "run_id"),
    RequiredColumn("payroll_account_mappings", "purpose_key"),
    RequiredColumn("payroll_employees", "birth_place"),
    RequiredColumn("payroll_employees", "tax_regime"),
    RequiredColumn("payroll_employee_compensation_profiles", "payroll_employee_id"),
    RequiredColumn("payroll_employee_payment_profiles", "payroll_employee_id"),
    RequiredColumn("payroll_employee_deduction_profiles", "payroll_employee_id"),
    RequiredColumn("payroll_employee_deduction_profiles", "fonacot_discount_type"),
    RequiredColumn("payroll_employee_deduction_profiles", "fonacot_discount_value"),
    RequiredColumn("payroll_employee_deduction_profiles", "fonacot_start_date"),
    RequiredColumn("payroll_employee_benefit_profiles", "payroll_employee_id"),
    RequiredColumn("payroll_employee_address_profiles", "payroll_employee_id"),
    RequiredColumn("payroll_concepts", "concept_key"),
    RequiredColumn("payroll_concept_rules", "concept_id"),
    RequiredColumn("payroll_sat_catalog_entries", "sat_group"),
    RequiredColumn("payroll_sat_concept_mappings", "concept_key"),
    RequiredColumn("support_tickets", "asunto"),
    RequiredColumn("support_tickets", "estado"),
    RequiredColumn("support_tickets", "requester_empleado_id"),
    RequiredColumn("support_ticket_comments", "ticket_id"),
    RequiredColumn("support_ticket_comments", "body"),
)


REQUIRED_INDEXES: Sequence[RequiredIndex] = (
    RequiredIndex("expense_reports", "idx_expense_reports_origen"),
    RequiredIndex("expense_reports", "idx_expense_reports_solicitud_documento_id"),
    RequiredIndex("expense_reports", "idx_expense_reports_informe_documento_id"),
    RequiredIndex("expense_reports", "idx_expense_reports_cuenta_contable_id"),
    RequiredIndex("expense_reports", "idx_expense_reports_contra_cuenta_contable_id"),
    RequiredIndex("expense_reports", "idx_expense_reports_cfdi_uuid_manual"),
    RequiredIndex("expense_reports", "idx_expense_reports_cfdi_report_id"),
    RequiredIndex("expense_reports", "idx_expense_reports_referencia_base"),
    RequiredIndex("expense_reports", "idx_expense_reports_cuenta_gastos_id"),
    RequiredIndex("documentos", "idx_documentos_proveedor_cliente_id"),
    RequiredIndex("copa_telmex_players", "idx_copa_telmex_players_team_roster_index"),
    RequiredIndex("copa_telmex_teams", "idx_copa_telmex_teams_tournament_slug"),
    RequiredIndex("assistant_conversations", "ix_assistant_conversations_empleado_id"),
    RequiredIndex(
        "assistant_conversations", "ix_assistant_conversations_tournament_key"
    ),
    RequiredIndex("assistant_conversations", "ix_assistant_conversations_archived"),
    RequiredIndex("assistant_messages", "ix_assistant_messages_conversation_id"),
    RequiredIndex("assistant_messages", "ix_assistant_messages_tool_name"),
    RequiredIndex("assistant_runs", "ix_assistant_runs_conversation_id"),
    RequiredIndex("assistant_runs", "ix_assistant_runs_empleado_id"),
    RequiredIndex("assistant_runs", "ix_assistant_runs_status"),
    RequiredIndex("assistant_artifacts", "ix_assistant_artifacts_conversation_id"),
    RequiredIndex(
        "assistant_artifacts", "ix_assistant_artifacts_created_by_empleado_id"
    ),
    RequiredIndex("assistant_artifacts", "ix_assistant_artifacts_artifact_type"),
    RequiredIndex("analyst_cases", "idx_analyst_cases_user_id"),
    RequiredIndex("analyst_cases", "idx_analyst_cases_status"),
    RequiredIndex("analyst_cases", "idx_analyst_cases_updated_at"),
    RequiredIndex("analyst_case_versions", "idx_analyst_case_versions_case_id"),
    RequiredIndex("empleados", "idx_empleados_aprobador_id"),
    RequiredIndex("documentos", "idx_documentos_beneficiario_empleado_id"),
    RequiredIndex("documentos", "idx_documentos_gasto_generado_id"),
    RequiredIndex("documentos", "idx_documentos_referencia_base"),
    RequiredIndex("documentos", "idx_documentos_cuenta_gastos_id"),
    RequiredIndex("documentos", "idx_documentos_cfdi_uuid_manual"),
    RequiredIndex("documentos", "idx_documentos_cfdi_report_id"),
    RequiredIndex("access_profiles", "ux_access_profiles_profile_key"),
    RequiredIndex("access_profiles", "ix_access_profiles_active"),
    RequiredIndex("empleado_access_profiles", "ux_empleado_access_profiles_unique"),
    RequiredIndex(
        "empleado_access_profiles", "ix_empleado_access_profiles_empleado_id"
    ),
    RequiredIndex("empleado_access_profiles", "ix_empleado_access_profiles_profile_id"),
    RequiredIndex(
        "reconciliation_audit_logs", "ix_reconciliation_audit_logs_bank_movement_id"
    ),
    RequiredIndex(
        "reconciliation_audit_logs", "ix_reconciliation_audit_logs_empleado_id"
    ),
    RequiredIndex("reconciliation_audit_logs", "ix_reconciliation_audit_logs_action"),
    RequiredIndex("regulatory_sources", "ux_regulatory_sources_source_key"),
    RequiredIndex("labor_rule_snapshots", "ux_labor_rule_snapshots_rule_effective"),
    RequiredIndex(
        "tax_tables_isr", "ux_tax_tables_isr_regime_periodicity_effective_row"
    ),
    RequiredIndex(
        "tax_tables_subsidio_empleo", "ux_tax_tables_subsidio_periodicity_effective"
    ),
    RequiredIndex("social_security_tables", "ux_social_security_component_effective"),
    RequiredIndex("payroll_employers", "ux_payroll_employers_employer_key"),
    RequiredIndex(
        "payroll_employer_registrations", "ux_payroll_employer_registrations_code"
    ),
    RequiredIndex("payroll_employees", "ux_payroll_employees_empleado_id"),
    RequiredIndex("payroll_periods", "ux_payroll_periods_type_year_no"),
    RequiredIndex("payroll_incidents", "ix_payroll_incidents_period_id"),
    RequiredIndex("payroll_runs", "ix_payroll_runs_period_id"),
    RequiredIndex("payroll_run_lines", "ux_payroll_run_lines_run_employee"),
    RequiredIndex("payroll_account_mappings", "ux_payroll_account_mappings_unique"),
    RequiredIndex(
        "payroll_employee_compensation_profiles",
        "ux_payroll_employee_compensation_profiles_employee",
    ),
    RequiredIndex(
        "payroll_employee_payment_profiles",
        "ux_payroll_employee_payment_profiles_employee",
    ),
    RequiredIndex(
        "payroll_employee_deduction_profiles",
        "ux_payroll_employee_deduction_profiles_employee",
    ),
    RequiredIndex(
        "payroll_employee_benefit_profiles",
        "ux_payroll_employee_benefit_profiles_employee",
    ),
    RequiredIndex(
        "payroll_employee_address_profiles",
        "ux_payroll_employee_address_profiles_employee",
    ),
    RequiredIndex("payroll_concepts", "ux_payroll_concepts_concept_key"),
    RequiredIndex(
        "payroll_concept_rules", "ux_payroll_concept_rules_concept_effective"
    ),
    RequiredIndex(
        "payroll_sat_catalog_entries", "ux_payroll_sat_catalog_entries_group_code"
    ),
    RequiredIndex(
        "payroll_sat_concept_mappings", "ux_payroll_sat_concept_mappings_unique"
    ),
    RequiredIndex("support_tickets", "ix_support_tickets_requester_empleado_id"),
    RequiredIndex("support_tickets", "ix_support_tickets_estado"),
    RequiredIndex("support_tickets", "ix_support_tickets_created_at"),
    RequiredIndex(
        "support_ticket_comments", "ix_support_ticket_comments_ticket_id"
    ),
    RequiredIndex(
        "support_ticket_comments", "ix_support_ticket_comments_created_at"
    ),
)


SCHEMA_PATCHES: Sequence[Tuple[str, str]] = (
    (
        "create_accounting_import_runs_table",
        """
        CREATE TABLE IF NOT EXISTS accounting_import_runs (
            id UUID PRIMARY KEY,
            source_type VARCHAR(50) NOT NULL,
            filename VARCHAR(255) NOT NULL,
            source_sha256 VARCHAR(64) NULL,
            mode VARCHAR(20) NOT NULL DEFAULT 'apply',
            status VARCHAR(20) NOT NULL DEFAULT 'completed',
            started_by_empleado_id UUID NULL REFERENCES empleados(id) ON DELETE SET NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ NULL,
            summary_json JSONB NULL,
            error_text TEXT NULL
        )
        """,
    ),
    (
        "create_accounting_polizas_table",
        """
        CREATE TABLE IF NOT EXISTS accounting_polizas (
            id UUID PRIMARY KEY,
            import_run_id UUID NULL REFERENCES accounting_import_runs(id) ON DELETE SET NULL,
            source_file VARCHAR(255) NOT NULL,
            source_sheet VARCHAR(120) NULL,
            source_row_start INTEGER NULL,
            tipo_poliza VARCHAR(20) NOT NULL,
            numero_poliza VARCHAR(50) NOT NULL,
            fecha_poliza TIMESTAMP NULL,
            beneficiario_nombre VARCHAR(500) NULL,
            concepto TEXT NOT NULL,
            concepto_resumen TEXT NULL,
            line_count_declared INTEGER NULL,
            line_count_actual INTEGER NULL,
            cfdi_uuid VARCHAR(100) NULL,
            cfdi_report_id UUID NULL REFERENCES cfdi_reports(id) ON DELETE SET NULL,
            origen VARCHAR(50) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_accounting_poliza_lines_table",
        """
        CREATE TABLE IF NOT EXISTS accounting_poliza_lines (
            id UUID PRIMARY KEY,
            poliza_id UUID NOT NULL REFERENCES accounting_polizas(id) ON DELETE CASCADE,
            line_no INTEGER NOT NULL,
            cuenta_codigo VARCHAR(100) NOT NULL,
            cuenta_contable_id UUID NULL REFERENCES cuentas_contables(id) ON DELETE SET NULL,
            concepto TEXT NULL,
            movimiento_no VARCHAR(20) NULL,
            debe DOUBLE PRECISION NULL,
            haber DOUBLE PRECISION NULL,
            raw_row_json JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_accounting_close_periods_table",
        """
        CREATE TABLE IF NOT EXISTS accounting_close_periods (
            id UUID PRIMARY KEY,
            fiscal_year INTEGER NOT NULL,
            fiscal_month INTEGER NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            notes TEXT NULL,
            closed_at TIMESTAMPTZ NULL,
            closed_by_empleado_id UUID NULL REFERENCES empleados(id) ON DELETE SET NULL,
            reopened_at TIMESTAMPTZ NULL,
            reopened_by_empleado_id UUID NULL REFERENCES empleados(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_accounting_audit_logs_table",
        """
        CREATE TABLE IF NOT EXISTS accounting_audit_logs (
            id UUID PRIMARY KEY,
            empleado_id UUID NULL REFERENCES empleados(id) ON DELETE SET NULL,
            poliza_id UUID NULL REFERENCES accounting_polizas(id) ON DELETE SET NULL,
            poliza_line_id UUID NULL REFERENCES accounting_poliza_lines(id) ON DELETE SET NULL,
            close_period_id UUID NULL REFERENCES accounting_close_periods(id) ON DELETE SET NULL,
            entity_type VARCHAR(50) NOT NULL,
            action VARCHAR(50) NOT NULL,
            before_state JSONB NULL,
            after_state JSONB NULL,
            details JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_accounting_close_checklist_items_table",
        """
        CREATE TABLE IF NOT EXISTS accounting_close_checklist_items (
            id UUID PRIMARY KEY,
            close_period_id UUID NOT NULL REFERENCES accounting_close_periods(id) ON DELETE CASCADE,
            task_code VARCHAR(80) NOT NULL,
            label VARCHAR(255) NOT NULL,
            owner_role VARCHAR(80) NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            notes TEXT NULL,
            completed_at TIMESTAMPTZ NULL,
            completed_by_empleado_id UUID NULL REFERENCES empleados(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_aux_ledger_entries_table",
        """
        CREATE TABLE IF NOT EXISTS aux_ledger_entries (
            id UUID PRIMARY KEY,
            import_run_id UUID NULL REFERENCES accounting_import_runs(id) ON DELETE SET NULL,
            source_file VARCHAR(255) NOT NULL,
            source_sheet VARCHAR(120) NULL,
            source_row_number INTEGER NOT NULL,
            cuenta_codigo VARCHAR(100) NOT NULL,
            cuenta_nombre VARCHAR(500) NULL,
            cuenta_contable_id UUID NULL REFERENCES cuentas_contables(id) ON DELETE SET NULL,
            tipo_poliza VARCHAR(20) NULL,
            numero_poliza VARCHAR(50) NULL,
            fecha TIMESTAMP NULL,
            concepto TEXT NULL,
            saldo_inicial DOUBLE PRECISION NULL,
            debe DOUBLE PRECISION NULL,
            haber DOUBLE PRECISION NULL,
            saldo DOUBLE PRECISION NULL,
            cfdi_uuid VARCHAR(100) NULL,
            related_poliza_id UUID NULL REFERENCES accounting_polizas(id) ON DELETE SET NULL,
            raw_row_json JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_bank_movements_table",
        """
        CREATE TABLE IF NOT EXISTS bank_movements (
            id UUID PRIMARY KEY,
            import_run_id UUID NULL REFERENCES accounting_import_runs(id) ON DELETE SET NULL,
            source_file VARCHAR(255) NOT NULL,
            source_row_number INTEGER NOT NULL,
            cuenta_bancaria VARCHAR(100) NULL,
            fecha TIMESTAMP NULL,
            hora VARCHAR(10) NULL,
            sucursal VARCHAR(20) NULL,
            descripcion VARCHAR(255) NULL,
            signo VARCHAR(1) NULL,
            importe DOUBLE PRECISION NULL,
            saldo DOUBLE PRECISION NULL,
            referencia_bancaria VARCHAR(100) NULL,
            concepto_banco TEXT NULL,
            banco_participante VARCHAR(255) NULL,
            clabe_beneficiario VARCHAR(32) NULL,
            nombre_beneficiario VARCHAR(255) NULL,
            cuenta_ordenante VARCHAR(100) NULL,
            nombre_ordenante VARCHAR(255) NULL,
            codigo_devolucion VARCHAR(50) NULL,
            causa_devolucion VARCHAR(255) NULL,
            rfc_beneficiario VARCHAR(20) NULL,
            rfc_ordenante VARCHAR(20) NULL,
            clave_rastreo VARCHAR(120) NULL,
            descripcion_larga TEXT NULL,
            proveedor_cliente_id UUID NULL REFERENCES proveedores_clientes(id) ON DELETE SET NULL,
            matched_aux_entry_id UUID NULL REFERENCES aux_ledger_entries(id) ON DELETE SET NULL,
            related_poliza_id UUID NULL REFERENCES accounting_polizas(id) ON DELETE SET NULL,
            matched_expense_id UUID NULL REFERENCES expense_reports(id) ON DELETE SET NULL,
            conciliacion_estado VARCHAR(20) NOT NULL DEFAULT 'unmatched',
            raw_row_json JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_aux_ledger_entries_table",
        """
        CREATE TABLE IF NOT EXISTS aux_ledger_entries (
            id UUID PRIMARY KEY,
            import_run_id UUID NULL REFERENCES accounting_import_runs(id) ON DELETE SET NULL,
            source_file VARCHAR(255) NOT NULL,
            source_sheet VARCHAR(120) NULL,
            source_row_number INTEGER NOT NULL,
            cuenta_codigo VARCHAR(100) NOT NULL,
            cuenta_nombre VARCHAR(500) NULL,
            cuenta_contable_id UUID NULL REFERENCES cuentas_contables(id) ON DELETE SET NULL,
            tipo_poliza VARCHAR(20) NULL,
            numero_poliza VARCHAR(50) NULL,
            fecha TIMESTAMP NULL,
            concepto TEXT NULL,
            saldo_inicial DOUBLE PRECISION NULL,
            debe DOUBLE PRECISION NULL,
            haber DOUBLE PRECISION NULL,
            saldo DOUBLE PRECISION NULL,
            cfdi_uuid VARCHAR(100) NULL,
            related_poliza_id UUID NULL REFERENCES accounting_polizas(id) ON DELETE SET NULL,
            raw_row_json JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_assistant_conversations_table",
        """
        CREATE TABLE IF NOT EXISTS assistant_conversations (
            id UUID PRIMARY KEY,
            empleado_id UUID NOT NULL REFERENCES empleados(id),
            title VARCHAR(200) NULL,
            tournament_key VARCHAR(50) NULL,
            archived BOOLEAN NOT NULL DEFAULT FALSE,
            "metadata" JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_assistant_messages_table",
        """
        CREATE TABLE IF NOT EXISTS assistant_messages (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES assistant_conversations(id) ON DELETE CASCADE,
            role VARCHAR(20) NOT NULL,
            content TEXT NULL,
            tool_name VARCHAR(100) NULL,
            tool_payload JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_assistant_runs_table",
        """
        CREATE TABLE IF NOT EXISTS assistant_runs (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES assistant_conversations(id) ON DELETE CASCADE,
            empleado_id UUID NOT NULL REFERENCES empleados(id),
            status VARCHAR(50) NOT NULL DEFAULT 'completed',
            model VARCHAR(100) NULL,
            user_message TEXT NULL,
            assistant_message TEXT NULL,
            tool_trace JSONB NULL,
            pending_tool_name VARCHAR(100) NULL,
            pending_tool_args JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_assistant_artifacts_table",
        """
        CREATE TABLE IF NOT EXISTS assistant_artifacts (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES assistant_conversations(id) ON DELETE CASCADE,
            created_by_empleado_id UUID NOT NULL REFERENCES empleados(id),
            title VARCHAR(200) NOT NULL,
            artifact_type VARCHAR(50) NOT NULL DEFAULT 'report_template',
            format VARCHAR(20) NOT NULL DEFAULT 'markdown',
            content TEXT NOT NULL,
            "metadata" JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_analyst_cases_table",
        """
        CREATE TABLE IF NOT EXISTS analyst_cases (
            case_id VARCHAR(80) PRIMARY KEY,
            user_id VARCHAR(120) NOT NULL,
            role VARCHAR(80) NOT NULL,
            question TEXT NOT NULL,
            analyst_intent JSONB NOT NULL,
            status VARCHAR(40) NOT NULL,
            evidence JSONB NOT NULL,
            current_answer TEXT NOT NULL,
            next_questions JSONB NOT NULL,
            suggested_routes JSONB NOT NULL,
            caveats JSONB NOT NULL,
            writes_policy JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by VARCHAR(120) NULL,
            closed_at TIMESTAMPTZ NULL,
            closed_by VARCHAR(120) NULL,
            CONSTRAINT check_analyst_cases_status
                CHECK (
                    status IN (
                        'open',
                        'waiting_context',
                        'analyzed',
                        'reviewed',
                        'closed'
                    )
                )
        )
        """,
    ),
    (
        "create_analyst_case_versions_table",
        """
        CREATE TABLE IF NOT EXISTS analyst_case_versions (
            version_id VARCHAR(96) PRIMARY KEY,
            case_id VARCHAR(80) NOT NULL
                REFERENCES analyst_cases(case_id) ON DELETE CASCADE,
            version_number INTEGER NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by VARCHAR(120) NOT NULL,
            status VARCHAR(40) NOT NULL,
            answer TEXT NOT NULL,
            evidence JSONB NOT NULL,
            next_questions JSONB NOT NULL,
            suggested_routes JSONB NOT NULL,
            caveats JSONB NOT NULL,
            answer_contract JSONB NOT NULL,
            changed_fields JSONB NOT NULL,
            CONSTRAINT ux_analyst_case_versions_case_version
                UNIQUE (case_id, version_number),
            CONSTRAINT check_analyst_case_versions_status
                CHECK (
                    status IN (
                        'open',
                        'waiting_context',
                        'analyzed',
                        'reviewed',
                        'closed'
                    )
                )
        )
        """,
    ),
    (
        "create_idx_analyst_cases_user_id",
        (
            "CREATE INDEX IF NOT EXISTS idx_analyst_cases_user_id "
            "ON analyst_cases(user_id)"
        ),
    ),
    (
        "create_idx_analyst_cases_status",
        (
            "CREATE INDEX IF NOT EXISTS idx_analyst_cases_status "
            "ON analyst_cases(status)"
        ),
    ),
    (
        "create_idx_analyst_cases_updated_at",
        (
            "CREATE INDEX IF NOT EXISTS idx_analyst_cases_updated_at "
            "ON analyst_cases(updated_at)"
        ),
    ),
    (
        "create_idx_analyst_case_versions_case_id",
        (
            "CREATE INDEX IF NOT EXISTS idx_analyst_case_versions_case_id "
            "ON analyst_case_versions(case_id)"
        ),
    ),
    (
        "create_access_profiles_table",
        """
        CREATE TABLE IF NOT EXISTS access_profiles (
            id UUID PRIMARY KEY,
            profile_key VARCHAR(80) NOT NULL UNIQUE,
            name VARCHAR(120) NOT NULL,
            description TEXT NULL,
            base_role VARCHAR(50) NOT NULL DEFAULT 'empleado',
            permissions JSONB NOT NULL DEFAULT '{}'::jsonb,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_empleado_id UUID NULL REFERENCES empleados(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_empleado_access_profiles_table",
        """
        CREATE TABLE IF NOT EXISTS empleado_access_profiles (
            id UUID PRIMARY KEY,
            empleado_id UUID NOT NULL REFERENCES empleados(id) ON DELETE CASCADE,
            profile_id UUID NOT NULL REFERENCES access_profiles(id) ON DELETE CASCADE,
            is_primary BOOLEAN NOT NULL DEFAULT FALSE,
            assigned_by_empleado_id UUID NULL REFERENCES empleados(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (empleado_id, profile_id)
        )
        """,
    ),
    (
        "create_reconciliation_audit_logs_table",
        """
        CREATE TABLE IF NOT EXISTS reconciliation_audit_logs (
            id UUID PRIMARY KEY,
            bank_movement_id UUID NOT NULL REFERENCES bank_movements(id) ON DELETE CASCADE,
            empleado_id UUID NULL REFERENCES empleados(id) ON DELETE SET NULL,
            action VARCHAR(50) NOT NULL,
            before_state JSONB NULL,
            after_state JSONB NULL,
            details JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_regulatory_sources_table",
        """
        CREATE TABLE IF NOT EXISTS regulatory_sources (
            id UUID PRIMARY KEY,
            source_key VARCHAR(120) NOT NULL UNIQUE,
            source_type VARCHAR(40) NOT NULL,
            authority VARCHAR(120) NOT NULL,
            title VARCHAR(500) NOT NULL,
            url VARCHAR(600) NOT NULL UNIQUE,
            legal_reference VARCHAR(255) NULL,
            verification_status VARCHAR(30) NOT NULL DEFAULT 'verified',
            published_at DATE NULL,
            effective_from DATE NULL,
            effective_to DATE NULL,
            summary_json JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_labor_rule_snapshots_table",
        """
        CREATE TABLE IF NOT EXISTS labor_rule_snapshots (
            id UUID PRIMARY KEY,
            source_id UUID NULL REFERENCES regulatory_sources(id) ON DELETE SET NULL,
            rule_key VARCHAR(120) NOT NULL,
            category VARCHAR(60) NOT NULL,
            title VARCHAR(255) NOT NULL,
            legal_reference VARCHAR(255) NULL,
            effective_from DATE NOT NULL,
            effective_to DATE NULL,
            numeric_value DOUBLE PRECISION NULL,
            unit VARCHAR(40) NULL,
            payload_json JSONB NULL,
            notes TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_tax_tables_isr_table",
        """
        CREATE TABLE IF NOT EXISTS tax_tables_isr (
            id UUID PRIMARY KEY,
            source_id UUID NULL REFERENCES regulatory_sources(id) ON DELETE SET NULL,
            regime_key VARCHAR(60) NOT NULL DEFAULT 'payroll_retention',
            periodicity VARCHAR(30) NOT NULL,
            effective_from DATE NOT NULL,
            effective_to DATE NULL,
            row_order INTEGER NOT NULL,
            lower_limit DOUBLE PRECISION NOT NULL,
            upper_limit DOUBLE PRECISION NULL,
            fixed_fee DOUBLE PRECISION NOT NULL DEFAULT 0,
            marginal_rate DOUBLE PRECISION NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_tax_tables_subsidio_empleo_table",
        """
        CREATE TABLE IF NOT EXISTS tax_tables_subsidio_empleo (
            id UUID PRIMARY KEY,
            source_id UUID NULL REFERENCES regulatory_sources(id) ON DELETE SET NULL,
            periodicity VARCHAR(30) NOT NULL,
            effective_from DATE NOT NULL,
            effective_to DATE NULL,
            income_limit DOUBLE PRECISION NOT NULL,
            subsidy_amount DOUBLE PRECISION NULL,
            subsidy_percent DOUBLE PRECISION NULL,
            uma_value DOUBLE PRECISION NULL,
            uma_periodicity VARCHAR(30) NULL,
            legal_reference VARCHAR(255) NULL,
            notes TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_social_security_tables_table",
        """
        CREATE TABLE IF NOT EXISTS social_security_tables (
            id UUID PRIMARY KEY,
            source_id UUID NULL REFERENCES regulatory_sources(id) ON DELETE SET NULL,
            component_key VARCHAR(120) NOT NULL,
            component_name VARCHAR(255) NOT NULL,
            branch VARCHAR(60) NOT NULL,
            calculation_mode VARCHAR(30) NOT NULL DEFAULT 'rate',
            base_type VARCHAR(30) NOT NULL,
            employer_rate DOUBLE PRECISION NULL,
            employee_rate DOUBLE PRECISION NULL,
            fixed_amount DOUBLE PRECISION NULL,
            min_uma DOUBLE PRECISION NULL,
            max_uma DOUBLE PRECISION NULL,
            legal_reference VARCHAR(255) NULL,
            formula_json JSONB NULL,
            effective_from DATE NOT NULL,
            effective_to DATE NULL,
            notes TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_payroll_employers_table",
        """
        CREATE TABLE IF NOT EXISTS payroll_employers (
            id UUID PRIMARY KEY,
            employer_key VARCHAR(80) NOT NULL UNIQUE,
            legal_name VARCHAR(255) NOT NULL,
            rfc VARCHAR(13) NULL UNIQUE,
            payroll_mode VARCHAR(40) NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            metadata_json JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_payroll_employer_registrations_table",
        """
        CREATE TABLE IF NOT EXISTS payroll_employer_registrations (
            id UUID PRIMARY KEY,
            payroll_employer_id UUID NOT NULL REFERENCES payroll_employers(id) ON DELETE CASCADE,
            registration_code VARCHAR(40) NOT NULL UNIQUE,
            branch_name VARCHAR(160) NULL,
            risk_class VARCHAR(20) NULL,
            risk_premium DOUBLE PRECISION NULL,
            effective_from DATE NULL,
            effective_to DATE NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            notes TEXT NULL,
            metadata_json JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_payroll_employees_table",
        """
        CREATE TABLE IF NOT EXISTS payroll_employees (
            id UUID PRIMARY KEY,
            empleado_id UUID NOT NULL UNIQUE REFERENCES empleados(id) ON DELETE CASCADE,
            employee_number VARCHAR(40) NULL,
            curp VARCHAR(30) NULL,
            rfc VARCHAR(20) NULL,
            nss VARCHAR(20) NULL,
            hire_date DATE NULL,
            seniority_date DATE NULL,
            contract_type VARCHAR(40) NOT NULL DEFAULT 'indeterminado',
            payroll_frequency VARCHAR(20) NOT NULL DEFAULT 'quincenal',
            salary_zone VARCHAR(20) NOT NULL DEFAULT 'general',
            payment_method VARCHAR(40) NULL,
            bank_name VARCHAR(120) NULL,
            bank_account_last4 VARCHAR(4) NULL,
            job_title VARCHAR(120) NULL,
            department_name VARCHAR(120) NULL,
            daily_salary DOUBLE PRECISION NOT NULL DEFAULT 0,
            integrated_daily_salary DOUBLE PRECISION NULL,
            variable_salary DOUBLE PRECISION NOT NULL DEFAULT 0,
            work_risk_class VARCHAR(20) NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            metadata_json JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_payroll_periods_table",
        """
        CREATE TABLE IF NOT EXISTS payroll_periods (
            id UUID PRIMARY KEY,
            period_type VARCHAR(20) NOT NULL,
            fiscal_year INTEGER NOT NULL,
            period_no INTEGER NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            payment_date DATE NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            notes TEXT NULL,
            created_by_empleado_id UUID NULL REFERENCES empleados(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_payroll_employee_compensation_profiles_table",
        """
        CREATE TABLE IF NOT EXISTS payroll_employee_compensation_profiles (
            id UUID PRIMARY KEY,
            payroll_employee_id UUID NOT NULL UNIQUE REFERENCES payroll_employees(id) ON DELETE CASCADE,
            compensation_regime VARCHAR(80) NULL,
            salary_type VARCHAR(40) NULL,
            monthly_net_salary DOUBLE PRECISION NULL,
            daily_salary DOUBLE PRECISION NULL,
            integrated_daily_salary DOUBLE PRECISION NULL,
            variable_salary DOUBLE PRECISION NULL,
            severance_daily_salary DOUBLE PRECISION NULL,
            work_risk_class VARCHAR(20) NULL,
            metadata_json JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_payroll_employee_payment_profiles_table",
        """
        CREATE TABLE IF NOT EXISTS payroll_employee_payment_profiles (
            id UUID PRIMARY KEY,
            payroll_employee_id UUID NOT NULL UNIQUE REFERENCES payroll_employees(id) ON DELETE CASCADE,
            payment_method VARCHAR(30) NULL,
            bank_name VARCHAR(120) NULL,
            account_number VARCHAR(32) NULL,
            clabe VARCHAR(18) NULL,
            customer_number VARCHAR(40) NULL,
            metadata_json JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_payroll_employee_deduction_profiles_table",
        """
        CREATE TABLE IF NOT EXISTS payroll_employee_deduction_profiles (
            id UUID PRIMARY KEY,
            payroll_employee_id UUID NOT NULL UNIQUE REFERENCES payroll_employees(id) ON DELETE CASCADE,
            deduction_name VARCHAR(160) NULL,
            infonavit_discount_type VARCHAR(80) NULL,
            infonavit_discount_value DOUBLE PRECISION NULL,
            infonavit_notice_folio VARCHAR(80) NULL,
            infonavit_credit_number VARCHAR(80) NULL,
            infonavit_start_date DATE NULL,
            loan_balance DOUBLE PRECISION NULL,
            monthly_deduction_amount DOUBLE PRECISION NULL,
            payroll_deduction_name VARCHAR(160) NULL,
            fonacot_credit_folio VARCHAR(80) NULL,
            fonacot_discount_type VARCHAR(80) NULL,
            fonacot_discount_value DOUBLE PRECISION NULL,
            fonacot_start_date DATE NULL,
            alimony_percentage DOUBLE PRECISION NULL,
            alimony_mode VARCHAR(40) NULL,
            alimony_fixed_amount DOUBLE PRECISION NULL,
            alimony_case_number VARCHAR(120) NULL,
            alimony_beneficiary_name VARCHAR(160) NULL,
            alimony_beneficiary_bank VARCHAR(120) NULL,
            alimony_beneficiary_account VARCHAR(64) NULL,
            alimony_beneficiary_clabe VARCHAR(18) NULL,
            alimony_effective_from DATE NULL,
            alimony_effective_to DATE NULL,
            alimony_apply_to_extraordinary BOOLEAN NOT NULL DEFAULT FALSE,
            alimony_priority_order INTEGER NULL,
            alimony_court_name VARCHAR(160) NULL,
            alimony_office_reference VARCHAR(160) NULL,
            metadata_json JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_payroll_employee_benefit_profiles_table",
        """
        CREATE TABLE IF NOT EXISTS payroll_employee_benefit_profiles (
            id UUID PRIMARY KEY,
            payroll_employee_id UUID NOT NULL UNIQUE REFERENCES payroll_employees(id) ON DELETE CASCADE,
            vacation_balance DOUBLE PRECISION NULL,
            umf VARCHAR(40) NULL,
            voucher_provider VARCHAR(120) NULL,
            voucher_account_number VARCHAR(64) NULL,
            voucher_card_number VARCHAR(64) NULL,
            metadata_json JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_payroll_employee_address_profiles_table",
        """
        CREATE TABLE IF NOT EXISTS payroll_employee_address_profiles (
            id UUID PRIMARY KEY,
            payroll_employee_id UUID NOT NULL UNIQUE REFERENCES payroll_employees(id) ON DELETE CASCADE,
            street VARCHAR(160) NULL,
            exterior_number VARCHAR(40) NULL,
            interior_number VARCHAR(40) NULL,
            neighborhood VARCHAR(120) NULL,
            municipality VARCHAR(120) NULL,
            state VARCHAR(120) NULL,
            postal_code VARCHAR(10) NULL,
            metadata_json JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_payroll_incidents_table",
        """
        CREATE TABLE IF NOT EXISTS payroll_incidents (
            id UUID PRIMARY KEY,
            payroll_employee_id UUID NOT NULL REFERENCES payroll_employees(id) ON DELETE CASCADE,
            period_id UUID NOT NULL REFERENCES payroll_periods(id) ON DELETE CASCADE,
            incident_type VARCHAR(40) NOT NULL,
            incident_code VARCHAR(60) NULL,
            quantity DOUBLE PRECISION NOT NULL DEFAULT 1,
            taxable_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
            exempt_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
            description TEXT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'captured',
            payload_json JSONB NULL,
            created_by_empleado_id UUID NULL REFERENCES empleados(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_payroll_runs_table",
        """
        CREATE TABLE IF NOT EXISTS payroll_runs (
            id UUID PRIMARY KEY,
            period_id UUID NOT NULL REFERENCES payroll_periods(id) ON DELETE CASCADE,
            run_type VARCHAR(30) NOT NULL DEFAULT 'nomina_ordinaria',
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            notes TEXT NULL,
            source_snapshot_tag VARCHAR(80) NULL,
            gross_total DOUBLE PRECISION NOT NULL DEFAULT 0,
            deductions_total DOUBLE PRECISION NOT NULL DEFAULT 0,
            employer_charges_total DOUBLE PRECISION NOT NULL DEFAULT 0,
            net_total DOUBLE PRECISION NOT NULL DEFAULT 0,
            created_by_empleado_id UUID NULL REFERENCES empleados(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_payroll_account_mappings_table",
        """
        CREATE TABLE IF NOT EXISTS payroll_account_mappings (
            id UUID PRIMARY KEY,
            payroll_employer_id UUID NULL REFERENCES payroll_employers(id) ON DELETE CASCADE,
            purpose_key VARCHAR(80) NOT NULL,
            cuenta_contable_id UUID NOT NULL REFERENCES cuentas_contables(id) ON DELETE SET NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            notes TEXT NULL,
            created_by_empleado_id UUID NULL REFERENCES empleados(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_payroll_run_lines_table",
        """
        CREATE TABLE IF NOT EXISTS payroll_run_lines (
            id UUID PRIMARY KEY,
            run_id UUID NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
            payroll_employee_id UUID NOT NULL REFERENCES payroll_employees(id) ON DELETE CASCADE,
            days_paid DOUBLE PRECISION NOT NULL DEFAULT 0,
            taxable_total DOUBLE PRECISION NOT NULL DEFAULT 0,
            exempt_total DOUBLE PRECISION NOT NULL DEFAULT 0,
            deductions_total DOUBLE PRECISION NOT NULL DEFAULT 0,
            employer_charges_total DOUBLE PRECISION NOT NULL DEFAULT 0,
            isr_withheld DOUBLE PRECISION NOT NULL DEFAULT 0,
            subsidy_applied DOUBLE PRECISION NOT NULL DEFAULT 0,
            net_pay DOUBLE PRECISION NOT NULL DEFAULT 0,
            integrated_daily_salary_used DOUBLE PRECISION NULL,
            perceptions_json JSONB NULL,
            deductions_json JSONB NULL,
            employer_charges_json JSONB NULL,
            incidents_summary JSONB NULL,
            notes TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_payroll_concepts_table",
        """
        CREATE TABLE IF NOT EXISTS payroll_concepts (
            id UUID PRIMARY KEY,
            concept_key VARCHAR(80) NOT NULL UNIQUE,
            name VARCHAR(160) NOT NULL,
            concept_type VARCHAR(30) NOT NULL,
            input_mode VARCHAR(30) NOT NULL DEFAULT 'amount',
            tax_group VARCHAR(40) NULL,
            affects_sbc BOOLEAN NOT NULL DEFAULT FALSE,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            display_order INTEGER NOT NULL DEFAULT 100,
            aliases_json JSONB NULL,
            metadata_json JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_payroll_concept_rules_table",
        """
        CREATE TABLE IF NOT EXISTS payroll_concept_rules (
            id UUID PRIMARY KEY,
            concept_id UUID NOT NULL REFERENCES payroll_concepts(id) ON DELETE CASCADE,
            source_id UUID NULL REFERENCES regulatory_sources(id) ON DELETE SET NULL,
            effective_from DATE NOT NULL,
            effective_to DATE NULL,
            taxable_mode VARCHAR(40) NOT NULL DEFAULT 'fully_taxable',
            exempt_formula_key VARCHAR(80) NULL,
            taxable_formula_key VARCHAR(80) NULL,
            sbc_mode VARCHAR(40) NOT NULL DEFAULT 'ignore',
            exempt_cap_multiplier DOUBLE PRECISION NULL,
            exempt_cap_unit VARCHAR(30) NULL,
            payload_json JSONB NULL,
            notes TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_payroll_sat_catalog_entries_table",
        """
        CREATE TABLE IF NOT EXISTS payroll_sat_catalog_entries (
            id UUID PRIMARY KEY,
            sat_group VARCHAR(30) NOT NULL,
            code VARCHAR(10) NOT NULL,
            description VARCHAR(200) NOT NULL,
            official_source_url TEXT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            notes TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_payroll_sat_concept_mappings_table",
        """
        CREATE TABLE IF NOT EXISTS payroll_sat_concept_mappings (
            id UUID PRIMARY KEY,
            concept_key VARCHAR(80) NOT NULL,
            sat_group VARCHAR(30) NOT NULL,
            sat_code VARCHAR(10) NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            mapping_basis VARCHAR(40) NOT NULL DEFAULT 'default_seed',
            notes TEXT NULL,
            created_by_empleado_id UUID NULL REFERENCES empleados(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "create_budget_concepts_table",
        """
        CREATE TABLE IF NOT EXISTS budget_concepts (
            id UUID PRIMARY KEY,
            tournament_id UUID NULL REFERENCES tournaments(id) ON UPDATE CASCADE ON DELETE SET NULL,
            tournament_code VARCHAR(40) NULL,
            tournament_name VARCHAR(200) NOT NULL,
            concept_name VARCHAR(200) NOT NULL,
            concept_key VARCHAR(200) NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            source VARCHAR(80) NOT NULL DEFAULT 'manual',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by_empleado_id UUID NULL REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "expense_reports_origen_column",
        "ALTER TABLE IF EXISTS expense_reports ADD COLUMN IF NOT EXISTS origen VARCHAR(50) NULL",
    ),
    (
        "expense_reports_numero_factura_column",
        "ALTER TABLE IF EXISTS expense_reports ADD COLUMN IF NOT EXISTS numero_factura TEXT NULL",
    ),
    (
        "expense_reports_solicitud_documento_id_column",
        "ALTER TABLE IF EXISTS expense_reports ADD COLUMN IF NOT EXISTS solicitud_documento_id UUID NULL REFERENCES documentos(id) ON UPDATE CASCADE ON DELETE SET NULL",
    ),
    (
        "expense_reports_informe_documento_id_column",
        "ALTER TABLE IF EXISTS expense_reports ADD COLUMN IF NOT EXISTS informe_documento_id UUID NULL REFERENCES documentos(id) ON UPDATE CASCADE ON DELETE SET NULL",
    ),
    (
        "expense_reports_cuenta_contable_id_column",
        "ALTER TABLE IF EXISTS expense_reports ADD COLUMN IF NOT EXISTS cuenta_contable_id UUID NULL REFERENCES cuentas_contables(id) ON UPDATE CASCADE ON DELETE SET NULL",
    ),
    (
        "expense_reports_contra_cuenta_contable_id_column",
        "ALTER TABLE IF EXISTS expense_reports ADD COLUMN IF NOT EXISTS contra_cuenta_contable_id UUID NULL REFERENCES cuentas_contables(id) ON UPDATE CASCADE ON DELETE SET NULL",
    ),
    (
        "expense_reports_cfdi_uuid_manual_column",
        "ALTER TABLE IF EXISTS expense_reports ADD COLUMN IF NOT EXISTS cfdi_uuid_manual TEXT NULL",
    ),
    (
        "expense_reports_cfdi_report_id_column",
        "ALTER TABLE IF EXISTS expense_reports ADD COLUMN IF NOT EXISTS cfdi_report_id UUID NULL REFERENCES cfdi_reports(id) ON UPDATE CASCADE ON DELETE SET NULL",
    ),
    (
        "expense_reports_referencia_base_column",
        "ALTER TABLE IF EXISTS expense_reports ADD COLUMN IF NOT EXISTS referencia_base TEXT NULL",
    ),
    (
        "expense_reports_cuenta_gastos_id_column",
        "ALTER TABLE IF EXISTS expense_reports ADD COLUMN IF NOT EXISTS cuenta_gastos_id UUID NULL REFERENCES cuentas_de_gastos(id) ON UPDATE CASCADE ON DELETE SET NULL",
    ),
    (
        "expense_reports_pagado_con_amex_empresa_column",
        "ALTER TABLE IF EXISTS expense_reports ADD COLUMN IF NOT EXISTS pagado_con_amex_empresa BOOLEAN NULL",
    ),
    (
        "expense_reports_categorias_column",
        "ALTER TABLE IF EXISTS expense_reports ADD COLUMN IF NOT EXISTS categorias JSONB NULL",
    ),
    (
        "expense_reports_edicion_column",
        "ALTER TABLE IF EXISTS expense_reports ADD COLUMN IF NOT EXISTS edicion INTEGER NULL",
    ),
    (
        "expense_reports_currency_column",
        "ALTER TABLE IF EXISTS expense_reports ADD COLUMN IF NOT EXISTS currency VARCHAR(3) NOT NULL DEFAULT 'MXN'",
    ),
    (
        "expense_reports_budget_concept_id_column",
        "ALTER TABLE IF EXISTS expense_reports ADD COLUMN IF NOT EXISTS budget_concept_id UUID NULL REFERENCES budget_concepts(id) ON UPDATE CASCADE ON DELETE SET NULL",
    ),
    (
        "copa_players_roster_index_column",
        "ALTER TABLE IF EXISTS copa_telmex_players ADD COLUMN IF NOT EXISTS roster_index INTEGER",
    ),
    (
        "copa_players_photo_sha256_column",
        "ALTER TABLE IF EXISTS copa_telmex_players ADD COLUMN IF NOT EXISTS photo_sha256 VARCHAR(64)",
    ),
    (
        "copa_players_photo_ahash_column",
        "ALTER TABLE IF EXISTS copa_telmex_players ADD COLUMN IF NOT EXISTS photo_ahash VARCHAR(16)",
    ),
    (
        "copa_teams_contact_email_column",
        "ALTER TABLE IF EXISTS copa_telmex_teams ADD COLUMN IF NOT EXISTS contact_email VARCHAR(150)",
    ),
    (
        "copa_teams_tournament_slug_column",
        "ALTER TABLE IF EXISTS copa_telmex_teams ADD COLUMN IF NOT EXISTS tournament_slug VARCHAR(80)",
    ),
    (
        "assistant_conversations_tournament_key_column",
        "ALTER TABLE IF EXISTS assistant_conversations ADD COLUMN IF NOT EXISTS tournament_key VARCHAR(50)",
    ),
    (
        "assistant_conversations_archived_column",
        "ALTER TABLE IF EXISTS assistant_conversations ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT FALSE",
    ),
    (
        "assistant_conversations_metadata_column",
        'ALTER TABLE IF EXISTS assistant_conversations ADD COLUMN IF NOT EXISTS "metadata" JSONB NULL',
    ),
    (
        "assistant_messages_tool_payload_column",
        "ALTER TABLE IF EXISTS assistant_messages ADD COLUMN IF NOT EXISTS tool_payload JSONB NULL",
    ),
    (
        "assistant_messages_tool_name_column",
        "ALTER TABLE IF EXISTS assistant_messages ADD COLUMN IF NOT EXISTS tool_name VARCHAR(100) NULL",
    ),
    (
        "assistant_runs_tool_trace_column",
        "ALTER TABLE IF EXISTS assistant_runs ADD COLUMN IF NOT EXISTS tool_trace JSONB NULL",
    ),
    (
        "assistant_runs_model_column",
        "ALTER TABLE IF EXISTS assistant_runs ADD COLUMN IF NOT EXISTS model VARCHAR(100) NULL",
    ),
    (
        "assistant_runs_pending_tool_name_column",
        "ALTER TABLE IF EXISTS assistant_runs ADD COLUMN IF NOT EXISTS pending_tool_name VARCHAR(100) NULL",
    ),
    (
        "assistant_runs_pending_tool_args_column",
        "ALTER TABLE IF EXISTS assistant_runs ADD COLUMN IF NOT EXISTS pending_tool_args JSONB NULL",
    ),
    (
        "assistant_artifacts_metadata_column",
        'ALTER TABLE IF EXISTS assistant_artifacts ADD COLUMN IF NOT EXISTS "metadata" JSONB NULL',
    ),
    (
        "assistant_artifacts_format_column",
        "ALTER TABLE IF EXISTS assistant_artifacts ADD COLUMN IF NOT EXISTS format VARCHAR(20) NOT NULL DEFAULT 'markdown'",
    ),
    (
        "empleados_password_hash_column",
        "ALTER TABLE IF EXISTS empleados ADD COLUMN IF NOT EXISTS password_hash TEXT NULL",
    ),
    (
        "empleados_aprobador_id_column",
        "ALTER TABLE IF EXISTS empleados ADD COLUMN IF NOT EXISTS aprobador_id UUID NULL",
    ),
    (
        "idx_empleados_aprobador_id",
        "CREATE INDEX IF NOT EXISTS idx_empleados_aprobador_id ON empleados(aprobador_id)",
    ),
    (
        "documentos_beneficiario_empleado_id_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS beneficiario_empleado_id UUID NULL",
    ),
    (
        "documentos_proveedor_cliente_id_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS proveedor_cliente_id UUID NULL REFERENCES proveedores_clientes(id) ON UPDATE CASCADE ON DELETE SET NULL",
    ),
    (
        "documentos_fecha_pago_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS fecha_pago DATE NULL",
    ),
    (
        "documentos_concepto_pago_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS concepto_pago TEXT NULL",
    ),
    (
        "documentos_numero_factura_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS numero_factura TEXT NULL",
    ),
    (
        "documentos_referencia_pago_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS referencia_pago TEXT NULL",
    ),
    (
        "documentos_metodo_pago_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS metodo_pago TEXT NULL",
    ),
    (
        "documentos_gasto_generado_id_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS gasto_generado_id UUID NULL REFERENCES expense_reports(id) ON UPDATE CASCADE ON DELETE SET NULL",
    ),
    (
        "documentos_referencia_base_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS referencia_base TEXT NULL",
    ),
    (
        "documentos_referencia_operaciones_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS referencia_operaciones TEXT NULL",
    ),
    (
        "documentos_cuenta_gastos_id_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS cuenta_gastos_id UUID NULL REFERENCES cuentas_de_gastos(id) ON UPDATE CASCADE ON DELETE SET NULL",
    ),
    (
        "documentos_cfdi_uuid_manual_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS cfdi_uuid_manual TEXT NULL",
    ),
    (
        "documentos_cfdi_report_id_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS cfdi_report_id UUID NULL REFERENCES cfdi_reports(id) ON UPDATE CASCADE ON DELETE SET NULL",
    ),
    (
        "documentos_fase_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS fase TEXT NULL",
    ),
    (
        "documentos_categorias_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS categorias JSONB NULL",
    ),
    (
        "documentos_edicion_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS edicion INTEGER NULL",
    ),
    (
        "documentos_currency_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS currency VARCHAR(3) NOT NULL DEFAULT 'MXN'",
    ),
    (
        "documentos_budget_concept_id_column",
        "ALTER TABLE IF EXISTS documentos ADD COLUMN IF NOT EXISTS budget_concept_id UUID NULL REFERENCES budget_concepts(id) ON UPDATE CASCADE ON DELETE SET NULL",
    ),
    (
        "cuentas_de_gastos_categorias_column",
        "ALTER TABLE IF EXISTS cuentas_de_gastos ADD COLUMN IF NOT EXISTS categorias JSONB NULL",
    ),
    (
        "cuentas_de_gastos_edicion_column",
        "ALTER TABLE IF EXISTS cuentas_de_gastos ADD COLUMN IF NOT EXISTS edicion INTEGER NULL",
    ),
    (
        "cuentas_de_gastos_currency_column",
        "ALTER TABLE IF EXISTS cuentas_de_gastos ADD COLUMN IF NOT EXISTS currency VARCHAR(3) NOT NULL DEFAULT 'MXN'",
    ),
    (
        "documentos_estado_check_rechazado",
        """
        DO $$
        DECLARE
            current_def TEXT;
        BEGIN
            SELECT pg_get_constraintdef(oid)
            INTO current_def
            FROM pg_constraint
            WHERE conrelid = 'documentos'::regclass
              AND conname = 'documentos_estado_check';

            IF current_def IS NOT NULL AND position('rechazado' in current_def) = 0 THEN
                ALTER TABLE documentos DROP CONSTRAINT documentos_estado_check;
                ALTER TABLE documentos
                    ADD CONSTRAINT documentos_estado_check
                    CHECK (
                        estado = ANY (
                            ARRAY[
                                'borrador'::text,
                                'enviado'::text,
                                'aprobado'::text,
                                'rechazado'::text,
                                'pagado'::text,
                                'cerrado'::text
                            ]
                        )
                    );
            END IF;
        END $$;
        """,
    ),
    (
        "tournaments_etapas_column",
        "ALTER TABLE IF EXISTS tournaments ADD COLUMN IF NOT EXISTS etapas JSONB NULL",
    ),
    (
        "tournaments_categorias_column",
        "ALTER TABLE IF EXISTS tournaments ADD COLUMN IF NOT EXISTS categorias JSONB NULL",
    ),
    (
        "tournaments_form_visibility_areas_column",
        "ALTER TABLE IF EXISTS tournaments ADD COLUMN IF NOT EXISTS form_visibility_areas JSONB NULL",
    ),
    (
        "idx_documentos_beneficiario_empleado_id",
        "CREATE INDEX IF NOT EXISTS idx_documentos_beneficiario_empleado_id ON documentos(beneficiario_empleado_id)",
    ),
    (
        "idx_documentos_proveedor_cliente_id",
        "CREATE INDEX IF NOT EXISTS idx_documentos_proveedor_cliente_id ON documentos(proveedor_cliente_id)",
    ),
    (
        "idx_documentos_gasto_generado_id",
        "CREATE INDEX IF NOT EXISTS idx_documentos_gasto_generado_id ON documentos(gasto_generado_id)",
    ),
    (
        "idx_documentos_referencia_base",
        "CREATE INDEX IF NOT EXISTS idx_documentos_referencia_base ON documentos(referencia_base)",
    ),
    (
        "idx_documentos_cuenta_gastos_id",
        "CREATE INDEX IF NOT EXISTS idx_documentos_cuenta_gastos_id ON documentos(cuenta_gastos_id)",
    ),
    (
        "idx_documentos_cfdi_uuid_manual",
        "CREATE INDEX IF NOT EXISTS idx_documentos_cfdi_uuid_manual ON documentos(cfdi_uuid_manual)",
    ),
    (
        "idx_documentos_cfdi_report_id",
        "CREATE INDEX IF NOT EXISTS idx_documentos_cfdi_report_id ON documentos(cfdi_report_id)",
    ),
    (
        "idx_documentos_fase",
        "CREATE INDEX IF NOT EXISTS idx_documentos_fase ON documentos(fase) WHERE fase IS NOT NULL",
    ),
    (
        "access_profiles_base_role_column",
        "ALTER TABLE IF EXISTS access_profiles ADD COLUMN IF NOT EXISTS base_role VARCHAR(50) NOT NULL DEFAULT 'empleado'",
    ),
    (
        "access_profiles_permissions_column",
        "ALTER TABLE IF EXISTS access_profiles ADD COLUMN IF NOT EXISTS permissions JSONB NOT NULL DEFAULT '{}'::jsonb",
    ),
    (
        "access_profiles_active_column",
        "ALTER TABLE IF EXISTS access_profiles ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE",
    ),
    (
        "access_profiles_description_column",
        "ALTER TABLE IF EXISTS access_profiles ADD COLUMN IF NOT EXISTS description TEXT NULL",
    ),
    (
        "access_profiles_profile_key_column",
        "ALTER TABLE IF EXISTS access_profiles ADD COLUMN IF NOT EXISTS profile_key VARCHAR(80)",
    ),
    (
        "access_profiles_name_column",
        "ALTER TABLE IF EXISTS access_profiles ADD COLUMN IF NOT EXISTS name VARCHAR(120)",
    ),
    (
        "access_profiles_created_by_column",
        "ALTER TABLE IF EXISTS access_profiles ADD COLUMN IF NOT EXISTS created_by_empleado_id UUID NULL REFERENCES empleados(id)",
    ),
    (
        "access_profiles_created_at_column",
        "ALTER TABLE IF EXISTS access_profiles ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    ),
    (
        "access_profiles_updated_at_column",
        "ALTER TABLE IF EXISTS access_profiles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    ),
    (
        "empleado_access_profiles_is_primary_column",
        "ALTER TABLE IF EXISTS empleado_access_profiles ADD COLUMN IF NOT EXISTS is_primary BOOLEAN NOT NULL DEFAULT FALSE",
    ),
    (
        "empleado_access_profiles_assigned_by_column",
        "ALTER TABLE IF EXISTS empleado_access_profiles ADD COLUMN IF NOT EXISTS assigned_by_empleado_id UUID NULL REFERENCES empleados(id)",
    ),
    (
        "empleado_access_profiles_created_at_column",
        "ALTER TABLE IF EXISTS empleado_access_profiles ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    ),
    (
        "payroll_employees_birth_date_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS birth_date DATE NULL",
    ),
    (
        "payroll_employees_birth_place_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS birth_place VARCHAR(120) NULL",
    ),
    (
        "payroll_employees_gender_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS gender VARCHAR(20) NULL",
    ),
    (
        "payroll_employees_tax_regime_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS tax_regime VARCHAR(160) NULL",
    ),
    (
        "payroll_employees_personal_email_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS personal_email VARCHAR(160) NULL",
    ),
    (
        "payroll_employees_work_email_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS work_email VARCHAR(160) NULL",
    ),
    (
        "payroll_employees_personal_postal_code_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS personal_postal_code VARCHAR(10) NULL",
    ),
    (
        "payroll_employees_contract_start_date_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS contract_start_date DATE NULL",
    ),
    (
        "payroll_employees_contract_end_date_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS contract_end_date DATE NULL",
    ),
    (
        "payroll_employees_employment_state_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS employment_state VARCHAR(120) NULL",
    ),
    (
        "payroll_employees_employee_type_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS employee_type VARCHAR(80) NULL",
    ),
    (
        "payroll_employees_policy_name_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS policy_name VARCHAR(120) NULL",
    ),
    (
        "payroll_employees_worker_type_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS worker_type VARCHAR(120) NULL",
    ),
    (
        "payroll_employees_geographic_area_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS geographic_area VARCHAR(60) NULL",
    ),
    (
        "payroll_employees_schedule_scheme_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS schedule_scheme VARCHAR(120) NULL",
    ),
    (
        "payroll_employees_reduced_workweek_type_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS reduced_workweek_type VARCHAR(120) NULL",
    ),
    (
        "payroll_employees_worked_days_override_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS worked_days_override DOUBLE PRECISION NULL",
    ),
    (
        "payroll_employees_employer_registration_id_column",
        "ALTER TABLE IF EXISTS payroll_employees ADD COLUMN IF NOT EXISTS employer_registration_id UUID NULL REFERENCES payroll_employer_registrations(id) ON DELETE SET NULL",
    ),
    (
        "payroll_employee_deduction_profiles_fonacot_discount_type_column",
        "ALTER TABLE IF EXISTS payroll_employee_deduction_profiles ADD COLUMN IF NOT EXISTS fonacot_discount_type VARCHAR(80) NULL",
    ),
    (
        "payroll_employee_deduction_profiles_fonacot_discount_value_column",
        "ALTER TABLE IF EXISTS payroll_employee_deduction_profiles ADD COLUMN IF NOT EXISTS fonacot_discount_value DOUBLE PRECISION NULL",
    ),
    (
        "payroll_employee_deduction_profiles_fonacot_start_date_column",
        "ALTER TABLE IF EXISTS payroll_employee_deduction_profiles ADD COLUMN IF NOT EXISTS fonacot_start_date DATE NULL",
    ),
    (
        "idx_expense_reports_origen",
        "CREATE INDEX IF NOT EXISTS idx_expense_reports_origen ON expense_reports(origen)",
    ),
    (
        "idx_expense_reports_solicitud_documento_id",
        "CREATE INDEX IF NOT EXISTS idx_expense_reports_solicitud_documento_id ON expense_reports(solicitud_documento_id)",
    ),
    (
        "idx_expense_reports_informe_documento_id",
        "CREATE INDEX IF NOT EXISTS idx_expense_reports_informe_documento_id ON expense_reports(informe_documento_id)",
    ),
    (
        "idx_expense_reports_cuenta_contable_id",
        "CREATE INDEX IF NOT EXISTS idx_expense_reports_cuenta_contable_id ON expense_reports(cuenta_contable_id)",
    ),
    (
        "idx_expense_reports_contra_cuenta_contable_id",
        "CREATE INDEX IF NOT EXISTS idx_expense_reports_contra_cuenta_contable_id ON expense_reports(contra_cuenta_contable_id)",
    ),
    (
        "idx_expense_reports_cfdi_uuid_manual",
        "CREATE INDEX IF NOT EXISTS idx_expense_reports_cfdi_uuid_manual ON expense_reports(cfdi_uuid_manual)",
    ),
    (
        "idx_expense_reports_cfdi_report_id",
        "CREATE INDEX IF NOT EXISTS idx_expense_reports_cfdi_report_id ON expense_reports(cfdi_report_id)",
    ),
    (
        "idx_expense_reports_referencia_base",
        "CREATE INDEX IF NOT EXISTS idx_expense_reports_referencia_base ON expense_reports(referencia_base)",
    ),
    (
        "idx_expense_reports_cuenta_gastos_id",
        "CREATE INDEX IF NOT EXISTS idx_expense_reports_cuenta_gastos_id ON expense_reports(cuenta_gastos_id)",
    ),
    (
        "idx_expense_reports_pagado_con_amex_empresa",
        "CREATE INDEX IF NOT EXISTS idx_expense_reports_pagado_con_amex_empresa ON expense_reports(pagado_con_amex_empresa)",
    ),
    (
        "idx_copa_telmex_players_team_roster_index",
        "CREATE INDEX IF NOT EXISTS idx_copa_telmex_players_team_roster_index ON copa_telmex_players(team_id, roster_index)",
    ),
    (
        "idx_copa_telmex_teams_tournament_slug",
        "CREATE INDEX IF NOT EXISTS idx_copa_telmex_teams_tournament_slug ON copa_telmex_teams(tournament_slug)",
    ),
    (
        "ix_accounting_import_runs_source_type",
        "CREATE INDEX IF NOT EXISTS ix_accounting_import_runs_source_type ON accounting_import_runs(source_type)",
    ),
    (
        "ix_accounting_import_runs_source_sha256",
        "CREATE INDEX IF NOT EXISTS ix_accounting_import_runs_source_sha256 ON accounting_import_runs(source_sha256)",
    ),
    (
        "ix_accounting_import_runs_started_by_empleado_id",
        "CREATE INDEX IF NOT EXISTS ix_accounting_import_runs_started_by_empleado_id ON accounting_import_runs(started_by_empleado_id)",
    ),
    (
        "ix_accounting_polizas_import_run_id",
        "CREATE INDEX IF NOT EXISTS ix_accounting_polizas_import_run_id ON accounting_polizas(import_run_id)",
    ),
    (
        "ix_accounting_polizas_tipo_poliza",
        "CREATE INDEX IF NOT EXISTS ix_accounting_polizas_tipo_poliza ON accounting_polizas(tipo_poliza)",
    ),
    (
        "ix_accounting_polizas_numero_poliza",
        "CREATE INDEX IF NOT EXISTS ix_accounting_polizas_numero_poliza ON accounting_polizas(numero_poliza)",
    ),
    (
        "ix_accounting_polizas_fecha_poliza",
        "CREATE INDEX IF NOT EXISTS ix_accounting_polizas_fecha_poliza ON accounting_polizas(fecha_poliza)",
    ),
    (
        "ix_accounting_polizas_beneficiario_nombre",
        "CREATE INDEX IF NOT EXISTS ix_accounting_polizas_beneficiario_nombre ON accounting_polizas(beneficiario_nombre)",
    ),
    (
        "ix_accounting_polizas_concepto_resumen",
        "CREATE INDEX IF NOT EXISTS ix_accounting_polizas_concepto_resumen ON accounting_polizas(concepto_resumen)",
    ),
    (
        "ix_accounting_polizas_cfdi_uuid",
        "CREATE INDEX IF NOT EXISTS ix_accounting_polizas_cfdi_uuid ON accounting_polizas(cfdi_uuid)",
    ),
    (
        "ix_accounting_polizas_cfdi_report_id",
        "CREATE INDEX IF NOT EXISTS ix_accounting_polizas_cfdi_report_id ON accounting_polizas(cfdi_report_id)",
    ),
    (
        "ix_accounting_polizas_origen",
        "CREATE INDEX IF NOT EXISTS ix_accounting_polizas_origen ON accounting_polizas(origen)",
    ),
    (
        "ux_accounting_polizas_source_natural_key",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_accounting_polizas_source_natural_key ON accounting_polizas(source_file, tipo_poliza, numero_poliza)",
    ),
    (
        "ix_accounting_poliza_lines_poliza_id",
        "CREATE INDEX IF NOT EXISTS ix_accounting_poliza_lines_poliza_id ON accounting_poliza_lines(poliza_id)",
    ),
    (
        "ix_accounting_poliza_lines_cuenta_codigo",
        "CREATE INDEX IF NOT EXISTS ix_accounting_poliza_lines_cuenta_codigo ON accounting_poliza_lines(cuenta_codigo)",
    ),
    (
        "ix_accounting_poliza_lines_cuenta_contable_id",
        "CREATE INDEX IF NOT EXISTS ix_accounting_poliza_lines_cuenta_contable_id ON accounting_poliza_lines(cuenta_contable_id)",
    ),
    (
        "ux_accounting_poliza_lines_unique",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_accounting_poliza_lines_unique ON accounting_poliza_lines(poliza_id, line_no)",
    ),
    (
        "ix_accounting_close_periods_fiscal_year",
        "CREATE INDEX IF NOT EXISTS ix_accounting_close_periods_fiscal_year ON accounting_close_periods(fiscal_year)",
    ),
    (
        "ix_accounting_close_periods_fiscal_month",
        "CREATE INDEX IF NOT EXISTS ix_accounting_close_periods_fiscal_month ON accounting_close_periods(fiscal_month)",
    ),
    (
        "ix_accounting_close_periods_status",
        "CREATE INDEX IF NOT EXISTS ix_accounting_close_periods_status ON accounting_close_periods(status)",
    ),
    (
        "ix_accounting_close_periods_closed_by",
        "CREATE INDEX IF NOT EXISTS ix_accounting_close_periods_closed_by ON accounting_close_periods(closed_by_empleado_id)",
    ),
    (
        "ix_accounting_close_periods_reopened_by",
        "CREATE INDEX IF NOT EXISTS ix_accounting_close_periods_reopened_by ON accounting_close_periods(reopened_by_empleado_id)",
    ),
    (
        "ux_accounting_close_periods_year_month",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_accounting_close_periods_year_month ON accounting_close_periods(fiscal_year, fiscal_month)",
    ),
    (
        "ix_accounting_audit_logs_empleado_id",
        "CREATE INDEX IF NOT EXISTS ix_accounting_audit_logs_empleado_id ON accounting_audit_logs(empleado_id)",
    ),
    (
        "ix_accounting_audit_logs_poliza_id",
        "CREATE INDEX IF NOT EXISTS ix_accounting_audit_logs_poliza_id ON accounting_audit_logs(poliza_id)",
    ),
    (
        "ix_accounting_audit_logs_poliza_line_id",
        "CREATE INDEX IF NOT EXISTS ix_accounting_audit_logs_poliza_line_id ON accounting_audit_logs(poliza_line_id)",
    ),
    (
        "ix_accounting_audit_logs_close_period_id",
        "CREATE INDEX IF NOT EXISTS ix_accounting_audit_logs_close_period_id ON accounting_audit_logs(close_period_id)",
    ),
    (
        "ix_accounting_audit_logs_entity_type",
        "CREATE INDEX IF NOT EXISTS ix_accounting_audit_logs_entity_type ON accounting_audit_logs(entity_type)",
    ),
    (
        "ix_accounting_audit_logs_action",
        "CREATE INDEX IF NOT EXISTS ix_accounting_audit_logs_action ON accounting_audit_logs(action)",
    ),
    (
        "ix_accounting_audit_logs_created_at",
        "CREATE INDEX IF NOT EXISTS ix_accounting_audit_logs_created_at ON accounting_audit_logs(created_at)",
    ),
    (
        "ix_accounting_close_checklist_items_close_period_id",
        "CREATE INDEX IF NOT EXISTS ix_accounting_close_checklist_items_close_period_id ON accounting_close_checklist_items(close_period_id)",
    ),
    (
        "ix_accounting_close_checklist_items_task_code",
        "CREATE INDEX IF NOT EXISTS ix_accounting_close_checklist_items_task_code ON accounting_close_checklist_items(task_code)",
    ),
    (
        "ix_accounting_close_checklist_items_owner_role",
        "CREATE INDEX IF NOT EXISTS ix_accounting_close_checklist_items_owner_role ON accounting_close_checklist_items(owner_role)",
    ),
    (
        "ix_accounting_close_checklist_items_status",
        "CREATE INDEX IF NOT EXISTS ix_accounting_close_checklist_items_status ON accounting_close_checklist_items(status)",
    ),
    (
        "ix_accounting_close_checklist_items_completed_by",
        "CREATE INDEX IF NOT EXISTS ix_accounting_close_checklist_items_completed_by ON accounting_close_checklist_items(completed_by_empleado_id)",
    ),
    (
        "ux_accounting_close_checklist_items_period_task",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_accounting_close_checklist_items_period_task ON accounting_close_checklist_items(close_period_id, task_code)",
    ),
    (
        "ix_aux_ledger_entries_import_run_id",
        "CREATE INDEX IF NOT EXISTS ix_aux_ledger_entries_import_run_id ON aux_ledger_entries(import_run_id)",
    ),
    (
        "ix_aux_ledger_entries_cuenta_codigo",
        "CREATE INDEX IF NOT EXISTS ix_aux_ledger_entries_cuenta_codigo ON aux_ledger_entries(cuenta_codigo)",
    ),
    (
        "ix_aux_ledger_entries_cuenta_contable_id",
        "CREATE INDEX IF NOT EXISTS ix_aux_ledger_entries_cuenta_contable_id ON aux_ledger_entries(cuenta_contable_id)",
    ),
    (
        "ix_aux_ledger_entries_tipo_poliza",
        "CREATE INDEX IF NOT EXISTS ix_aux_ledger_entries_tipo_poliza ON aux_ledger_entries(tipo_poliza)",
    ),
    (
        "ix_aux_ledger_entries_numero_poliza",
        "CREATE INDEX IF NOT EXISTS ix_aux_ledger_entries_numero_poliza ON aux_ledger_entries(numero_poliza)",
    ),
    (
        "ix_aux_ledger_entries_fecha",
        "CREATE INDEX IF NOT EXISTS ix_aux_ledger_entries_fecha ON aux_ledger_entries(fecha)",
    ),
    (
        "ix_aux_ledger_entries_cfdi_uuid",
        "CREATE INDEX IF NOT EXISTS ix_aux_ledger_entries_cfdi_uuid ON aux_ledger_entries(cfdi_uuid)",
    ),
    (
        "ix_aux_ledger_entries_related_poliza_id",
        "CREATE INDEX IF NOT EXISTS ix_aux_ledger_entries_related_poliza_id ON aux_ledger_entries(related_poliza_id)",
    ),
    (
        "ux_aux_ledger_entries_source_row",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_aux_ledger_entries_source_row ON aux_ledger_entries(source_file, cuenta_codigo, source_row_number)",
    ),
    (
        "ix_bank_movements_import_run_id",
        "CREATE INDEX IF NOT EXISTS ix_bank_movements_import_run_id ON bank_movements(import_run_id)",
    ),
    (
        "ix_bank_movements_cuenta_bancaria",
        "CREATE INDEX IF NOT EXISTS ix_bank_movements_cuenta_bancaria ON bank_movements(cuenta_bancaria)",
    ),
    (
        "ix_bank_movements_fecha",
        "CREATE INDEX IF NOT EXISTS ix_bank_movements_fecha ON bank_movements(fecha)",
    ),
    (
        "ix_bank_movements_signo",
        "CREATE INDEX IF NOT EXISTS ix_bank_movements_signo ON bank_movements(signo)",
    ),
    (
        "ix_bank_movements_importe",
        "CREATE INDEX IF NOT EXISTS ix_bank_movements_importe ON bank_movements(importe)",
    ),
    (
        "ix_bank_movements_referencia_bancaria",
        "CREATE INDEX IF NOT EXISTS ix_bank_movements_referencia_bancaria ON bank_movements(referencia_bancaria)",
    ),
    (
        "ix_bank_movements_clabe_beneficiario",
        "CREATE INDEX IF NOT EXISTS ix_bank_movements_clabe_beneficiario ON bank_movements(clabe_beneficiario)",
    ),
    (
        "ix_bank_movements_nombre_beneficiario",
        "CREATE INDEX IF NOT EXISTS ix_bank_movements_nombre_beneficiario ON bank_movements(nombre_beneficiario)",
    ),
    (
        "ix_bank_movements_rfc_beneficiario",
        "CREATE INDEX IF NOT EXISTS ix_bank_movements_rfc_beneficiario ON bank_movements(rfc_beneficiario)",
    ),
    (
        "ix_bank_movements_clave_rastreo",
        "CREATE INDEX IF NOT EXISTS ix_bank_movements_clave_rastreo ON bank_movements(clave_rastreo)",
    ),
    (
        "ix_bank_movements_proveedor_cliente_id",
        "CREATE INDEX IF NOT EXISTS ix_bank_movements_proveedor_cliente_id ON bank_movements(proveedor_cliente_id)",
    ),
    (
        "ix_bank_movements_matched_aux_entry_id",
        "CREATE INDEX IF NOT EXISTS ix_bank_movements_matched_aux_entry_id ON bank_movements(matched_aux_entry_id)",
    ),
    (
        "ix_bank_movements_related_poliza_id",
        "CREATE INDEX IF NOT EXISTS ix_bank_movements_related_poliza_id ON bank_movements(related_poliza_id)",
    ),
    (
        "ix_bank_movements_matched_expense_id",
        "CREATE INDEX IF NOT EXISTS ix_bank_movements_matched_expense_id ON bank_movements(matched_expense_id)",
    ),
    (
        "ix_bank_movements_conciliacion_estado",
        "CREATE INDEX IF NOT EXISTS ix_bank_movements_conciliacion_estado ON bank_movements(conciliacion_estado)",
    ),
    (
        "ux_bank_movements_source_row",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_bank_movements_source_row ON bank_movements(source_file, source_row_number)",
    ),
    (
        "ix_aux_ledger_entries_import_run_id",
        "CREATE INDEX IF NOT EXISTS ix_aux_ledger_entries_import_run_id ON aux_ledger_entries(import_run_id)",
    ),
    (
        "ix_aux_ledger_entries_cuenta_codigo",
        "CREATE INDEX IF NOT EXISTS ix_aux_ledger_entries_cuenta_codigo ON aux_ledger_entries(cuenta_codigo)",
    ),
    (
        "ix_aux_ledger_entries_cuenta_contable_id",
        "CREATE INDEX IF NOT EXISTS ix_aux_ledger_entries_cuenta_contable_id ON aux_ledger_entries(cuenta_contable_id)",
    ),
    (
        "ix_aux_ledger_entries_tipo_poliza",
        "CREATE INDEX IF NOT EXISTS ix_aux_ledger_entries_tipo_poliza ON aux_ledger_entries(tipo_poliza)",
    ),
    (
        "ix_aux_ledger_entries_numero_poliza",
        "CREATE INDEX IF NOT EXISTS ix_aux_ledger_entries_numero_poliza ON aux_ledger_entries(numero_poliza)",
    ),
    (
        "ix_aux_ledger_entries_fecha",
        "CREATE INDEX IF NOT EXISTS ix_aux_ledger_entries_fecha ON aux_ledger_entries(fecha)",
    ),
    (
        "ix_aux_ledger_entries_cfdi_uuid",
        "CREATE INDEX IF NOT EXISTS ix_aux_ledger_entries_cfdi_uuid ON aux_ledger_entries(cfdi_uuid)",
    ),
    (
        "ix_aux_ledger_entries_related_poliza_id",
        "CREATE INDEX IF NOT EXISTS ix_aux_ledger_entries_related_poliza_id ON aux_ledger_entries(related_poliza_id)",
    ),
    (
        "ux_aux_ledger_entries_source_row",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_aux_ledger_entries_source_row ON aux_ledger_entries(source_file, cuenta_codigo, source_row_number)",
    ),
    (
        "ix_assistant_conversations_empleado_id",
        "CREATE INDEX IF NOT EXISTS ix_assistant_conversations_empleado_id ON assistant_conversations(empleado_id)",
    ),
    (
        "ix_assistant_conversations_tournament_key",
        "CREATE INDEX IF NOT EXISTS ix_assistant_conversations_tournament_key ON assistant_conversations(tournament_key)",
    ),
    (
        "ix_assistant_conversations_archived",
        "CREATE INDEX IF NOT EXISTS ix_assistant_conversations_archived ON assistant_conversations(archived)",
    ),
    (
        "ix_assistant_messages_conversation_id",
        "CREATE INDEX IF NOT EXISTS ix_assistant_messages_conversation_id ON assistant_messages(conversation_id)",
    ),
    (
        "ix_assistant_messages_tool_name",
        "CREATE INDEX IF NOT EXISTS ix_assistant_messages_tool_name ON assistant_messages(tool_name)",
    ),
    (
        "ix_assistant_runs_conversation_id",
        "CREATE INDEX IF NOT EXISTS ix_assistant_runs_conversation_id ON assistant_runs(conversation_id)",
    ),
    (
        "ix_assistant_runs_empleado_id",
        "CREATE INDEX IF NOT EXISTS ix_assistant_runs_empleado_id ON assistant_runs(empleado_id)",
    ),
    (
        "ix_assistant_runs_status",
        "CREATE INDEX IF NOT EXISTS ix_assistant_runs_status ON assistant_runs(status)",
    ),
    (
        "ix_assistant_artifacts_conversation_id",
        "CREATE INDEX IF NOT EXISTS ix_assistant_artifacts_conversation_id ON assistant_artifacts(conversation_id)",
    ),
    (
        "ix_assistant_artifacts_created_by_empleado_id",
        "CREATE INDEX IF NOT EXISTS ix_assistant_artifacts_created_by_empleado_id ON assistant_artifacts(created_by_empleado_id)",
    ),
    (
        "ix_assistant_artifacts_artifact_type",
        "CREATE INDEX IF NOT EXISTS ix_assistant_artifacts_artifact_type ON assistant_artifacts(artifact_type)",
    ),
    (
        "ux_access_profiles_profile_key",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_access_profiles_profile_key ON access_profiles(profile_key)",
    ),
    (
        "ix_access_profiles_active",
        "CREATE INDEX IF NOT EXISTS ix_access_profiles_active ON access_profiles(active)",
    ),
    (
        "ux_empleado_access_profiles_unique",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_empleado_access_profiles_unique ON empleado_access_profiles(empleado_id, profile_id)",
    ),
    (
        "ix_empleado_access_profiles_empleado_id",
        "CREATE INDEX IF NOT EXISTS ix_empleado_access_profiles_empleado_id ON empleado_access_profiles(empleado_id)",
    ),
    (
        "ix_empleado_access_profiles_profile_id",
        "CREATE INDEX IF NOT EXISTS ix_empleado_access_profiles_profile_id ON empleado_access_profiles(profile_id)",
    ),
    (
        "ix_reconciliation_audit_logs_bank_movement_id",
        "CREATE INDEX IF NOT EXISTS ix_reconciliation_audit_logs_bank_movement_id ON reconciliation_audit_logs(bank_movement_id)",
    ),
    (
        "ix_reconciliation_audit_logs_empleado_id",
        "CREATE INDEX IF NOT EXISTS ix_reconciliation_audit_logs_empleado_id ON reconciliation_audit_logs(empleado_id)",
    ),
    (
        "ix_reconciliation_audit_logs_action",
        "CREATE INDEX IF NOT EXISTS ix_reconciliation_audit_logs_action ON reconciliation_audit_logs(action)",
    ),
    (
        "ux_regulatory_sources_source_key",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_regulatory_sources_source_key ON regulatory_sources(source_key)",
    ),
    (
        "ux_regulatory_sources_url",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_regulatory_sources_url ON regulatory_sources(url)",
    ),
    (
        "ix_regulatory_sources_authority",
        "CREATE INDEX IF NOT EXISTS ix_regulatory_sources_authority ON regulatory_sources(authority)",
    ),
    (
        "ix_regulatory_sources_effective_from",
        "CREATE INDEX IF NOT EXISTS ix_regulatory_sources_effective_from ON regulatory_sources(effective_from)",
    ),
    (
        "ix_labor_rule_snapshots_source_id",
        "CREATE INDEX IF NOT EXISTS ix_labor_rule_snapshots_source_id ON labor_rule_snapshots(source_id)",
    ),
    (
        "ix_labor_rule_snapshots_category",
        "CREATE INDEX IF NOT EXISTS ix_labor_rule_snapshots_category ON labor_rule_snapshots(category)",
    ),
    (
        "ix_labor_rule_snapshots_rule_key",
        "CREATE INDEX IF NOT EXISTS ix_labor_rule_snapshots_rule_key ON labor_rule_snapshots(rule_key)",
    ),
    (
        "ux_labor_rule_snapshots_rule_effective",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_labor_rule_snapshots_rule_effective ON labor_rule_snapshots(rule_key, effective_from, effective_to) NULLS NOT DISTINCT",
    ),
    (
        "ix_tax_tables_isr_source_id",
        "CREATE INDEX IF NOT EXISTS ix_tax_tables_isr_source_id ON tax_tables_isr(source_id)",
    ),
    (
        "ix_tax_tables_isr_periodicity",
        "CREATE INDEX IF NOT EXISTS ix_tax_tables_isr_periodicity ON tax_tables_isr(periodicity)",
    ),
    (
        "ix_tax_tables_isr_effective_from",
        "CREATE INDEX IF NOT EXISTS ix_tax_tables_isr_effective_from ON tax_tables_isr(effective_from)",
    ),
    (
        "ux_tax_tables_isr_regime_periodicity_effective_row",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_tax_tables_isr_regime_periodicity_effective_row ON tax_tables_isr(regime_key, periodicity, effective_from, row_order)",
    ),
    (
        "ix_tax_tables_subsidio_source_id",
        "CREATE INDEX IF NOT EXISTS ix_tax_tables_subsidio_source_id ON tax_tables_subsidio_empleo(source_id)",
    ),
    (
        "ix_tax_tables_subsidio_periodicity",
        "CREATE INDEX IF NOT EXISTS ix_tax_tables_subsidio_periodicity ON tax_tables_subsidio_empleo(periodicity)",
    ),
    (
        "ux_tax_tables_subsidio_periodicity_effective",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_tax_tables_subsidio_periodicity_effective ON tax_tables_subsidio_empleo(periodicity, effective_from)",
    ),
    (
        "ix_social_security_source_id",
        "CREATE INDEX IF NOT EXISTS ix_social_security_source_id ON social_security_tables(source_id)",
    ),
    (
        "ix_social_security_component_key",
        "CREATE INDEX IF NOT EXISTS ix_social_security_component_key ON social_security_tables(component_key)",
    ),
    (
        "ux_social_security_component_effective",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_social_security_component_effective ON social_security_tables(component_key, effective_from)",
    ),
    (
        "ux_payroll_employers_employer_key",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_payroll_employers_employer_key ON payroll_employers(employer_key)",
    ),
    (
        "ix_payroll_employers_active",
        "CREATE INDEX IF NOT EXISTS ix_payroll_employers_active ON payroll_employers(active)",
    ),
    (
        "ux_payroll_employer_registrations_code",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_payroll_employer_registrations_code ON payroll_employer_registrations(registration_code)",
    ),
    (
        "ix_payroll_employer_registrations_employer_id",
        "CREATE INDEX IF NOT EXISTS ix_payroll_employer_registrations_employer_id ON payroll_employer_registrations(payroll_employer_id)",
    ),
    (
        "ix_payroll_employer_registrations_active",
        "CREATE INDEX IF NOT EXISTS ix_payroll_employer_registrations_active ON payroll_employer_registrations(active)",
    ),
    (
        "ux_payroll_employees_empleado_id",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_payroll_employees_empleado_id ON payroll_employees(empleado_id)",
    ),
    (
        "ix_payroll_employees_active",
        "CREATE INDEX IF NOT EXISTS ix_payroll_employees_active ON payroll_employees(active)",
    ),
    (
        "ix_payroll_employees_payroll_frequency",
        "CREATE INDEX IF NOT EXISTS ix_payroll_employees_payroll_frequency ON payroll_employees(payroll_frequency)",
    ),
    (
        "ix_payroll_employees_employer_registration_id",
        "CREATE INDEX IF NOT EXISTS ix_payroll_employees_employer_registration_id ON payroll_employees(employer_registration_id)",
    ),
    (
        "ux_payroll_periods_type_year_no",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_payroll_periods_type_year_no ON payroll_periods(period_type, fiscal_year, period_no)",
    ),
    (
        "ix_payroll_periods_status",
        "CREATE INDEX IF NOT EXISTS ix_payroll_periods_status ON payroll_periods(status)",
    ),
    (
        "ix_payroll_periods_start_date",
        "CREATE INDEX IF NOT EXISTS ix_payroll_periods_start_date ON payroll_periods(start_date)",
    ),
    (
        "ix_payroll_incidents_payroll_employee_id",
        "CREATE INDEX IF NOT EXISTS ix_payroll_incidents_payroll_employee_id ON payroll_incidents(payroll_employee_id)",
    ),
    (
        "ix_payroll_incidents_period_id",
        "CREATE INDEX IF NOT EXISTS ix_payroll_incidents_period_id ON payroll_incidents(period_id)",
    ),
    (
        "ix_payroll_incidents_incident_type",
        "CREATE INDEX IF NOT EXISTS ix_payroll_incidents_incident_type ON payroll_incidents(incident_type)",
    ),
    (
        "ix_payroll_runs_period_id",
        "CREATE INDEX IF NOT EXISTS ix_payroll_runs_period_id ON payroll_runs(period_id)",
    ),
    (
        "ix_payroll_runs_status",
        "CREATE INDEX IF NOT EXISTS ix_payroll_runs_status ON payroll_runs(status)",
    ),
    (
        "ix_payroll_account_mappings_employer_id",
        "CREATE INDEX IF NOT EXISTS ix_payroll_account_mappings_employer_id ON payroll_account_mappings(payroll_employer_id)",
    ),
    (
        "ix_payroll_account_mappings_purpose_key",
        "CREATE INDEX IF NOT EXISTS ix_payroll_account_mappings_purpose_key ON payroll_account_mappings(purpose_key)",
    ),
    (
        "ix_payroll_account_mappings_active",
        "CREATE INDEX IF NOT EXISTS ix_payroll_account_mappings_active ON payroll_account_mappings(active)",
    ),
    (
        "ux_payroll_account_mappings_unique",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_payroll_account_mappings_unique ON payroll_account_mappings(COALESCE(payroll_employer_id, '00000000-0000-0000-0000-000000000000'::uuid), purpose_key)",
    ),
    (
        "ix_payroll_run_lines_run_id",
        "CREATE INDEX IF NOT EXISTS ix_payroll_run_lines_run_id ON payroll_run_lines(run_id)",
    ),
    (
        "ix_payroll_run_lines_payroll_employee_id",
        "CREATE INDEX IF NOT EXISTS ix_payroll_run_lines_payroll_employee_id ON payroll_run_lines(payroll_employee_id)",
    ),
    (
        "ux_payroll_run_lines_run_employee",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_payroll_run_lines_run_employee ON payroll_run_lines(run_id, payroll_employee_id)",
    ),
    (
        "ux_payroll_employee_compensation_profiles_employee",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_payroll_employee_compensation_profiles_employee ON payroll_employee_compensation_profiles(payroll_employee_id)",
    ),
    (
        "ux_payroll_employee_payment_profiles_employee",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_payroll_employee_payment_profiles_employee ON payroll_employee_payment_profiles(payroll_employee_id)",
    ),
    (
        "ux_payroll_employee_deduction_profiles_employee",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_payroll_employee_deduction_profiles_employee ON payroll_employee_deduction_profiles(payroll_employee_id)",
    ),
    (
        "ux_payroll_employee_benefit_profiles_employee",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_payroll_employee_benefit_profiles_employee ON payroll_employee_benefit_profiles(payroll_employee_id)",
    ),
    (
        "ux_payroll_employee_address_profiles_employee",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_payroll_employee_address_profiles_employee ON payroll_employee_address_profiles(payroll_employee_id)",
    ),
    (
        "ux_payroll_concepts_concept_key",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_payroll_concepts_concept_key ON payroll_concepts(concept_key)",
    ),
    (
        "ix_payroll_concepts_type",
        "CREATE INDEX IF NOT EXISTS ix_payroll_concepts_type ON payroll_concepts(concept_type)",
    ),
    (
        "ix_payroll_concepts_tax_group",
        "CREATE INDEX IF NOT EXISTS ix_payroll_concepts_tax_group ON payroll_concepts(tax_group)",
    ),
    (
        "ix_payroll_concepts_affects_sbc",
        "CREATE INDEX IF NOT EXISTS ix_payroll_concepts_affects_sbc ON payroll_concepts(affects_sbc)",
    ),
    (
        "ix_payroll_concepts_active",
        "CREATE INDEX IF NOT EXISTS ix_payroll_concepts_active ON payroll_concepts(active)",
    ),
    (
        "ix_payroll_concept_rules_concept_id",
        "CREATE INDEX IF NOT EXISTS ix_payroll_concept_rules_concept_id ON payroll_concept_rules(concept_id)",
    ),
    (
        "ix_payroll_concept_rules_source_id",
        "CREATE INDEX IF NOT EXISTS ix_payroll_concept_rules_source_id ON payroll_concept_rules(source_id)",
    ),
    (
        "ix_payroll_concept_rules_effective_from",
        "CREATE INDEX IF NOT EXISTS ix_payroll_concept_rules_effective_from ON payroll_concept_rules(effective_from)",
    ),
    (
        "ix_payroll_concept_rules_taxable_mode",
        "CREATE INDEX IF NOT EXISTS ix_payroll_concept_rules_taxable_mode ON payroll_concept_rules(taxable_mode)",
    ),
    (
        "ux_payroll_concept_rules_concept_effective",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_payroll_concept_rules_concept_effective ON payroll_concept_rules(concept_id, effective_from)",
    ),
    (
        "ux_payroll_sat_catalog_entries_group_code",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_payroll_sat_catalog_entries_group_code ON payroll_sat_catalog_entries(sat_group, code)",
    ),
    (
        "ix_payroll_sat_catalog_entries_active",
        "CREATE INDEX IF NOT EXISTS ix_payroll_sat_catalog_entries_active ON payroll_sat_catalog_entries(active)",
    ),
    (
        "ux_payroll_sat_concept_mappings_unique",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_payroll_sat_concept_mappings_unique ON payroll_sat_concept_mappings(concept_key, sat_group)",
    ),
    (
        "ix_payroll_sat_concept_mappings_active",
        "CREATE INDEX IF NOT EXISTS ix_payroll_sat_concept_mappings_active ON payroll_sat_concept_mappings(active)",
    ),
    (
        "create_support_tickets_table",
        """
        CREATE TABLE IF NOT EXISTS support_tickets (
            id UUID PRIMARY KEY,
            requester_empleado_id UUID NOT NULL REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE CASCADE,
            asunto VARCHAR(200) NOT NULL,
            descripcion TEXT NOT NULL,
            categoria VARCHAR(40) NOT NULL DEFAULT 'otro',
            prioridad VARCHAR(20) NOT NULL DEFAULT 'normal',
            estado VARCHAR(30) NOT NULL DEFAULT 'abierto',
            page_url VARCHAR(600) NULL,
            contact_email VARCHAR(200) NULL,
            assigned_to_empleado_id UUID NULL REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE SET NULL,
            resolution_note TEXT NULL,
            resolved_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_support_tickets_categoria
                CHECK (categoria IN ('bug','duda','solicitud','acceso','otro')),
            CONSTRAINT ck_support_tickets_prioridad
                CHECK (prioridad IN ('baja','normal','alta','urgente')),
            CONSTRAINT ck_support_tickets_estado
                CHECK (estado IN ('abierto','en_revision','en_progreso','resuelto','cerrado'))
        )
        """,
    ),
    (
        "ix_support_tickets_requester_empleado_id",
        "CREATE INDEX IF NOT EXISTS ix_support_tickets_requester_empleado_id ON support_tickets(requester_empleado_id)",
    ),
    (
        "ix_support_tickets_assigned_to_empleado_id",
        "CREATE INDEX IF NOT EXISTS ix_support_tickets_assigned_to_empleado_id ON support_tickets(assigned_to_empleado_id)",
    ),
    (
        "ix_support_tickets_estado",
        "CREATE INDEX IF NOT EXISTS ix_support_tickets_estado ON support_tickets(estado)",
    ),
    (
        "ix_support_tickets_prioridad",
        "CREATE INDEX IF NOT EXISTS ix_support_tickets_prioridad ON support_tickets(prioridad)",
    ),
    (
        "ix_support_tickets_categoria",
        "CREATE INDEX IF NOT EXISTS ix_support_tickets_categoria ON support_tickets(categoria)",
    ),
    (
        "ix_support_tickets_created_at",
        "CREATE INDEX IF NOT EXISTS ix_support_tickets_created_at ON support_tickets(created_at)",
    ),
    (
        "create_support_ticket_comments_table",
        """
        CREATE TABLE IF NOT EXISTS support_ticket_comments (
            id UUID PRIMARY KEY,
            ticket_id UUID NOT NULL REFERENCES support_tickets(id) ON UPDATE CASCADE ON DELETE CASCADE,
            author_empleado_id UUID NULL REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE SET NULL,
            author_role VARCHAR(30) NOT NULL DEFAULT 'requester',
            body TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_support_ticket_comments_role
                CHECK (author_role IN ('requester','staff','system'))
        )
        """,
    ),
    (
        "ix_support_ticket_comments_ticket_id",
        "CREATE INDEX IF NOT EXISTS ix_support_ticket_comments_ticket_id ON support_ticket_comments(ticket_id)",
    ),
    (
        "ix_support_ticket_comments_author_empleado_id",
        "CREATE INDEX IF NOT EXISTS ix_support_ticket_comments_author_empleado_id ON support_ticket_comments(author_empleado_id)",
    ),
    (
        "ix_support_ticket_comments_created_at",
        "CREATE INDEX IF NOT EXISTS ix_support_ticket_comments_created_at ON support_ticket_comments(created_at)",
    ),
    (
        "create_telegram_notification_outbox_table",
        """
        CREATE TABLE IF NOT EXISTS telegram_notification_outbox (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            notification_type VARCHAR(64) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            documento_id UUID NULL REFERENCES documentos(id) ON UPDATE CASCADE ON DELETE SET NULL,
            recipient_empleado_id UUID NULL REFERENCES empleados(id) ON UPDATE CASCADE ON DELETE SET NULL,
            telegram_chat_id BIGINT NULL,
            header_text TEXT NULL,
            body_preview TEXT NULL,
            error_message TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            sent_at TIMESTAMPTZ NULL,
            updated_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_telegram_notification_outbox_status
                CHECK (status IN ('pending','sent','failed','skipped'))
        )
        """,
    ),
    (
        "ix_telegram_notification_outbox_documento_id",
        "CREATE INDEX IF NOT EXISTS ix_telegram_notification_outbox_documento_id ON telegram_notification_outbox(documento_id)",
    ),
    (
        "ix_telegram_notification_outbox_recipient_empleado_id",
        "CREATE INDEX IF NOT EXISTS ix_telegram_notification_outbox_recipient_empleado_id ON telegram_notification_outbox(recipient_empleado_id)",
    ),
    (
        "ix_telegram_notification_outbox_created_at",
        "CREATE INDEX IF NOT EXISTS ix_telegram_notification_outbox_created_at ON telegram_notification_outbox(created_at)",
    ),
    (
        "telegram_notification_outbox_retry_count",
        "ALTER TABLE IF EXISTS telegram_notification_outbox ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "telegram_notification_outbox_next_retry_at",
        "ALTER TABLE IF EXISTS telegram_notification_outbox ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ NULL",
    ),
    (
        "ix_telegram_notification_outbox_next_retry_at",
        "CREATE INDEX IF NOT EXISTS ix_telegram_notification_outbox_next_retry_at ON telegram_notification_outbox(next_retry_at)",
    ),
)


async def _column_exists(conn: AsyncConnection, table: str, column: str) -> bool:
    return bool(
        await conn.scalar(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:table AND column_name=:column)"
            ),
            {"table": table, "column": column},
        )
    )


async def _index_exists(conn: AsyncConnection, table: str, index_name: str) -> bool:
    return bool(
        await conn.scalar(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_indexes "
                "WHERE schemaname='public' AND tablename=:table AND indexname=:index_name)"
            ),
            {"table": table, "index_name": index_name},
        )
    )


async def _patch_already_satisfied(conn: AsyncConnection, sql: str) -> bool:
    """Avoid DDL on objects already in place.

    PostgreSQL still requires table ownership for `ALTER TABLE ... ADD COLUMN IF NOT
    EXISTS` and `CREATE INDEX IF NOT EXISTS`. Several runtime tables are managed by
    migrations/owners outside the web role, so startup must first inspect metadata
    and skip satisfied patches instead of issuing harmless-looking DDL.
    """

    normalized = " ".join(sql.strip().split())
    column_match = re.match(
        r"ALTER\s+TABLE\s+IF\s+EXISTS\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+\"?([a-zA-Z_][a-zA-Z0-9_]*)\"?",
        normalized,
        flags=re.IGNORECASE,
    )
    if column_match:
        return await _column_exists(conn, column_match.group(1), column_match.group(2))

    index_match = re.match(
        r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+ON\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        normalized,
        flags=re.IGNORECASE,
    )
    if index_match:
        return await _index_exists(conn, index_match.group(2), index_match.group(1))

    return False


async def apply_schema_guard(
    conn: AsyncConnection,
    *,
    logger: Optional[Any] = None,
    strict: bool = False,
) -> Dict[str, Any]:
    """Apply idempotent schema patches and return execution report."""
    applied: List[str] = []
    skipped: List[str] = []
    failed: List[Dict[str, str]] = []
    for patch_name, sql in SCHEMA_PATCHES:
        try:
            if await _patch_already_satisfied(conn, sql):
                skipped.append(patch_name)
                continue
            # Run each patch inside its own savepoint so one optional DDL failure
            # does not poison the whole startup transaction.
            async with conn.begin_nested():
                await conn.execute(text(sql))
            applied.append(patch_name)
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            err = {"patch": patch_name, "error": str(exc)}
            failed.append(err)
            if logger is not None:
                logger.warning("Schema guard patch failed: %s :: %s", patch_name, exc)
            if strict:
                raise
    report = {
        "applied": applied,
        "skipped": skipped,
        "failed": failed,
        "applied_count": len(applied),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
    }
    if logger is not None:
        logger.info(
            "Schema guard applied: ok=%s skipped=%s failed=%s",
            report["applied_count"],
            report["skipped_count"],
            report["failed_count"],
        )
    return report


async def check_schema_health(conn: AsyncConnection) -> Dict[str, Any]:
    """Return missing required tables/columns/indexes for runtime-critical paths."""
    missing_tables: List[str] = []
    missing_columns: List[Dict[str, str]] = []
    missing_indexes: List[Dict[str, str]] = []

    required_tables = sorted(
        {c.table for c in REQUIRED_COLUMNS} | {i.table for i in REQUIRED_INDEXES}
    )
    for table in required_tables:
        exists = await conn.scalar(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=:table)"
            ),
            {"table": table},
        )
        if not exists:
            missing_tables.append(table)

    for item in REQUIRED_COLUMNS:
        exists = await conn.scalar(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:table AND column_name=:column)"
            ),
            {"table": item.table, "column": item.column},
        )
        if not exists:
            missing_columns.append({"table": item.table, "column": item.column})

    for item in REQUIRED_INDEXES:
        exists = await conn.scalar(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_indexes "
                "WHERE schemaname='public' AND tablename=:table AND indexname=:index_name)"
            ),
            {"table": item.table, "index_name": item.index},
        )
        if not exists:
            missing_indexes.append({"table": item.table, "index": item.index})

    ok = not missing_tables and not missing_columns and not missing_indexes
    return {
        "ok": ok,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "missing_indexes": missing_indexes,
    }
