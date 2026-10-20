"""Static owner-DDL receipt from disposable PostgreSQL; never runtime-learned."""

EXPECTED_CHECKS = {
    ("amex_direct_coverage_revisions", "amex_direct_coverage_revisions_check"): "CHECK "
    "(((period_start "
    "<= "
    "original_period_start) "
    "AND "
    "(period_end "
    ">= "
    "original_period_end)))",
    (
        "amex_direct_coverage_revisions",
        "amex_direct_coverage_revisions_expected_count_check",
    ): "CHECK "
    "((expected_count "
    "> "
    "0))",
    (
        "amex_direct_coverage_revisions",
        "amex_direct_coverage_revisions_expected_source_version_check",
    ): "CHECK "
    "((expected_source_version "
    "> "
    "0))",
    (
        "amex_direct_coverage_revisions",
        "amex_direct_coverage_revisions_membership_digest_check",
    ): "CHECK "
    "((length((membership_digest)::text) "
    "= "
    "64))",
    (
        "amex_direct_coverage_revisions",
        "amex_direct_coverage_revisions_preview_hash_check",
    ): "CHECK "
    "((length((preview_hash)::text) "
    "= "
    "64))",
    (
        "amex_direct_coverage_revisions",
        "amex_direct_coverage_revisions_reason_check",
    ): "CHECK "
    "((length(TRIM(BOTH "
    "FROM "
    "reason)) "
    "> "
    "0))",
    (
        "amex_direct_coverage_revisions",
        "amex_direct_coverage_revisions_request_hash_check",
    ): "CHECK "
    "((length((request_hash)::text) "
    "= "
    "64))",
    (
        "amex_direct_items",
        "amex_direct_items_check",
    ): "CHECK (((((movement_kind)::text = "
    "'debit'::text) AND "
    "(imported_expense_id IS NOT NULL)) "
    "OR (((movement_kind)::text <> "
    "'debit'::text) AND "
    "(imported_expense_id IS NULL))))",
    ("amex_direct_items", "amex_direct_items_movement_kind_check"): "CHECK "
    "(((movement_kind)::text "
    "= ANY "
    "((ARRAY['debit'::character "
    "varying, "
    "'credit'::character "
    "varying, "
    "'unknown'::character "
    "varying, "
    "'skipped'::character "
    "varying])::text[])))",
    ("amex_direct_items", "amex_direct_items_source_version_check"): "CHECK "
    "((source_version > "
    "0))",
    ("amex_direct_prior_coverages", "amex_direct_prior_coverages_check"): "CHECK "
    "(((amount > "
    "(0)::numeric) "
    "AND "
    "((currency)::text "
    "= "
    "'MXN'::text)))",
    ("amex_direct_prior_coverages", "amex_direct_prior_coverages_check1"): "CHECK "
    "(((cut_id IS "
    "NULL) <> "
    "(completion_receipt_id "
    "IS NULL)))",
    (
        "amex_direct_recovered_rows",
        "amex_direct_recovered_rows_currency_check",
    ): "CHECK "
    "(((currency)::text "
    "= "
    "'MXN'::text))",
    (
        "amex_direct_recovered_rows",
        "amex_direct_recovered_rows_movement_kind_check",
    ): "CHECK "
    "(((movement_kind)::text "
    "= "
    "'debit'::text))",
    (
        "amex_direct_recovered_rows",
        "amex_direct_recovered_rows_proof_hash_check",
    ): "CHECK "
    "((length((proof_hash)::text) "
    "= "
    "64))",
    (
        "amex_direct_recovered_rows",
        "amex_direct_recovered_rows_raw_signed_amount_check",
    ): "CHECK "
    "((raw_signed_amount "
    "<> "
    "(0)::numeric))",
    (
        "amex_expense_account_settlement_allocations",
        "amex_expense_account_settlement_allocations_amount_check",
    ): "CHECK "
    "((amount "
    "> "
    "(0)::numeric))",
    (
        "amex_expense_account_settlements",
        "amex_expense_account_settlements_check",
    ): "CHECK "
    "(((amount "
    "> "
    "(0)::numeric) "
    "AND "
    "((currency)::text "
    "= "
    "'MXN'::text)))",
    (
        "amex_merchant_identity_aliases",
        "amex_merchant_identity_aliases_version_check",
    ): "CHECK "
    "((version "
    "> "
    "0))",
    (
        "amex_recognition_consumptions",
        "amex_recognition_consumptions_amount_check",
    ): "CHECK "
    "((amount "
    "> "
    "(0)::numeric))",
    ("amex_recognition_consumptions", "ck_amex_bridge_never_posted"): "CHECK "
    "((((binding_mode)::text "
    "<> "
    "'report_bridge'::text) "
    "OR "
    "(accounting_poliza_id "
    "IS NULL)))",
    ("amex_recognition_consumptions", "ck_amex_consumption_source_anchor"): "CHECK "
    "(((((source_kind)::text "
    "= "
    "'statement'::text) "
    "AND "
    "(imported_expense_id "
    "IS NOT NULL) "
    "AND "
    "(source_report_expense_id "
    "IS NULL) AND "
    "((binding_mode)::text "
    "= ANY "
    "((ARRAY['legacy'::character "
    "varying, "
    "'report_bridge'::character "
    "varying, "
    "'direct'::character "
    "varying])::text[]))) "
    "OR "
    "(((source_kind)::text "
    "= "
    "'report'::text) "
    "AND "
    "(imported_expense_id "
    "IS NULL) AND "
    "(source_report_expense_id "
    "IS NOT NULL) "
    "AND "
    "((binding_mode)::text "
    "= "
    "'report'::text))))",
    (
        "amex_statement_direct_contexts",
        "amex_statement_direct_contexts_currency_check",
    ): "CHECK "
    "(((currency)::text "
    "= "
    "'MXN'::text))",
    (
        "amex_statement_direct_contexts",
        "amex_statement_direct_contexts_version_check",
    ): "CHECK "
    "((version "
    "> "
    "0))",
    ("amex_statement_import_items", "ck_amex_statement_item_status"): "CHECK "
    "(((((status)::text "
    "= "
    "'imported'::text) "
    "AND "
    "(imported_expense_id "
    "IS NOT NULL) AND "
    "(reason_code IS "
    "NULL)) OR "
    "(((status)::text = "
    "'skipped'::text) "
    "AND "
    "(imported_expense_id "
    "IS NULL) AND "
    "(reason_code IS "
    "NOT NULL)) OR "
    "(((workflow_mode)::text "
    "= 'direct'::text) "
    "AND "
    "((status)::text = "
    "'credit'::text) "
    "AND "
    "(imported_expense_id "
    "IS NULL)) OR "
    "(((workflow_mode)::text "
    "= 'direct'::text) "
    "AND "
    "((status)::text = "
    "'unknown'::text) "
    "AND "
    "(imported_expense_id "
    "IS NULL) AND "
    "(reason_code IS "
    "NOT NULL))))",
    (
        "amex_statement_import_items",
        "ck_amex_statement_row_number",
    ): "CHECK ((row_number "
    ">= 2))",
    ("amex_statement_imports", "ck_amex_statement_counts"): "CHECK "
    "(((((workflow_mode)::text = "
    "'legacy'::text) AND "
    "(imported_count > 0) AND "
    "(skipped_count >= 0) AND "
    "(credit_count = 0) AND "
    "(unknown_count = 0) AND "
    "(total_rows = "
    "(imported_count + "
    "skipped_count))) OR "
    "(((workflow_mode)::text = "
    "'direct'::text) AND "
    "(total_rows > 0) AND "
    "(imported_count >= 0) AND "
    "(skipped_count >= 0) AND "
    "(credit_count >= 0) AND "
    "(unknown_count >= 0) AND "
    "(total_rows = "
    "(((imported_count + "
    "skipped_count) + "
    "credit_count) + "
    "unknown_count)))))",
    ("amex_statement_imports", "ck_amex_statement_hash"): "CHECK "
    "((length((file_sha256)::text) "
    "= 64))",
    ("amex_statement_imports", "ck_amex_statement_period"): "CHECK ((period_end >= "
    "period_start))",
    (
        "amex_statement_source_files",
        "amex_statement_source_files_byte_size_check",
    ): "CHECK "
    "(((byte_size "
    "> 0) "
    "AND "
    "(byte_size "
    "<= "
    "10485760)))",
    ("amex_statement_source_files", "amex_statement_source_files_check"): "CHECK "
    "((byte_size = "
    "octet_length(content)))",
    (
        "amex_statement_source_files",
        "amex_statement_source_files_file_sha256_check",
    ): "CHECK "
    "((length((file_sha256)::text) "
    "= "
    "64))",
}

EXPECTED_KEYS = [
    ("amex_cfdi_status_receipts", "f", ("actor_id",), "empleados", ("id",), "r"),
    ("amex_cfdi_status_receipts", "f", ("cfdi_id",), "cfdi_reports", ("id",), "r"),
    ("amex_cfdi_status_receipts", "p", ("id",)),
    (
        "amex_direct_accounting_cuts",
        "f",
        ("accounting_poliza_id",),
        "accounting_polizas",
        ("id",),
        "r",
    ),
    ("amex_direct_accounting_cuts", "f", ("actor_id",), "empleados", ("id",), "r"),
    (
        "amex_direct_accounting_cuts",
        "f",
        ("context_id",),
        "amex_statement_direct_contexts",
        ("id",),
        "r",
    ),
    (
        "amex_direct_accounting_cuts",
        "f",
        ("import_id",),
        "amex_statement_imports",
        ("id",),
        "r",
    ),
    ("amex_direct_accounting_cuts", "p", ("id",)),
    ("amex_direct_accounting_cuts", "u", ("accounting_poliza_id",)),
    ("amex_direct_accounting_cuts", "u", ("import_id",)),
    ("amex_direct_completion_receipts", "f", ("actor_id",), "empleados", ("id",), "r"),
    (
        "amex_direct_completion_receipts",
        "f",
        ("context_id",),
        "amex_statement_direct_contexts",
        ("id",),
        "r",
    ),
    (
        "amex_direct_completion_receipts",
        "f",
        ("import_id",),
        "amex_statement_imports",
        ("id",),
        "r",
    ),
    ("amex_direct_completion_receipts", "p", ("id",)),
    ("amex_direct_completion_receipts", "u", ("import_id",)),
    ("amex_direct_coverage_revisions", "f", ("actor_id",), "empleados", ("id",), "r"),
    (
        "amex_direct_coverage_revisions",
        "f",
        ("import_id",),
        "amex_statement_imports",
        ("id",),
        "r",
    ),
    (
        "amex_direct_coverage_revisions",
        "f",
        ("source_file_id",),
        "amex_statement_source_files",
        ("id",),
        "r",
    ),
    ("amex_direct_coverage_revisions", "p", ("id",)),
    ("amex_direct_coverage_revisions", "u", ("import_id",)),
    (
        "amex_direct_cut_items",
        "f",
        ("accounting_poliza_id",),
        "accounting_polizas",
        ("id",),
        "r",
    ),
    (
        "amex_direct_cut_items",
        "f",
        ("consumption_id",),
        "amex_recognition_consumptions",
        ("id",),
        "r",
    ),
    (
        "amex_direct_cut_items",
        "f",
        ("cut_id",),
        "amex_direct_accounting_cuts",
        ("id",),
        "r",
    ),
    (
        "amex_direct_cut_items",
        "f",
        ("direct_item_id",),
        "amex_direct_items",
        ("id",),
        "r",
    ),
    (
        "amex_direct_cut_items",
        "f",
        ("import_item_id",),
        "amex_statement_import_items",
        ("id",),
        "r",
    ),
    (
        "amex_direct_cut_items",
        "f",
        ("imported_expense_id",),
        "expense_reports",
        ("id",),
        "r",
    ),
    ("amex_direct_cut_items", "p", ("id",)),
    ("amex_direct_cut_items", "u", ("cut_id", "direct_item_id")),
    ("amex_direct_cut_items", "u", ("direct_item_id",)),
    ("amex_direct_events", "f", ("actor_id",), "empleados", ("id",), "r"),
    ("amex_direct_events", "f", ("item_id",), "amex_direct_items", ("id",), "r"),
    ("amex_direct_events", "p", ("id",)),
    (
        "amex_direct_items",
        "f",
        ("budget_assignment_id",),
        "budget_movement_assignments",
        ("id",),
        "r",
    ),
    ("amex_direct_items", "f", ("budget_concept_id",), "budget_concepts", ("id",), "r"),
    ("amex_direct_items", "f", ("budget_line_id",), "budget_lines", ("id",), "r"),
    ("amex_direct_items", "f", ("budget_version_id",), "budget_versions", ("id",), "r"),
    ("amex_direct_items", "f", ("cfdi_id",), "cfdi_reports", ("id",), "r"),
    (
        "amex_direct_items",
        "f",
        ("context_id",),
        "amex_statement_direct_contexts",
        ("id",),
        "r",
    ),
    ("amex_direct_items", "f", ("import_id",), "amex_statement_imports", ("id",), "r"),
    (
        "amex_direct_items",
        "f",
        ("import_item_id",),
        "amex_statement_import_items",
        ("id",),
        "r",
    ),
    (
        "amex_direct_items",
        "f",
        ("imported_expense_id",),
        "expense_reports",
        ("id",),
        "r",
    ),
    (
        "amex_direct_items",
        "f",
        ("merchant_alias_id",),
        "amex_merchant_identity_aliases",
        ("id",),
        "r",
    ),
    (
        "amex_direct_items",
        "f",
        ("status_receipt_id",),
        "amex_cfdi_status_receipts",
        ("id",),
        "r",
    ),
    ("amex_direct_items", "f", ("tournament_id",), "tournaments", ("id",), "r"),
    ("amex_direct_items", "p", ("id",)),
    ("amex_direct_items", "u", ("import_item_id",)),
    ("amex_direct_items", "u", ("imported_expense_id",)),
    (
        "amex_direct_prior_coverages",
        "f",
        ("accounting_poliza_id",),
        "accounting_polizas",
        ("id",),
        "r",
    ),
    ("amex_direct_prior_coverages", "f", ("actor_id",), "empleados", ("id",), "r"),
    (
        "amex_direct_prior_coverages",
        "f",
        ("card_account_id",),
        "amex_card_accounts",
        ("id",),
        "r",
    ),
    (
        "amex_direct_prior_coverages",
        "f",
        ("completion_receipt_id",),
        "amex_direct_completion_receipts",
        ("id",),
        "r",
    ),
    (
        "amex_direct_prior_coverages",
        "f",
        ("consumption_id",),
        "amex_recognition_consumptions",
        ("id",),
        "r",
    ),
    (
        "amex_direct_prior_coverages",
        "f",
        ("context_id",),
        "amex_statement_direct_contexts",
        ("id",),
        "r",
    ),
    (
        "amex_direct_prior_coverages",
        "f",
        ("cut_id",),
        "amex_direct_accounting_cuts",
        ("id",),
        "r",
    ),
    (
        "amex_direct_prior_coverages",
        "f",
        ("direct_item_id",),
        "amex_direct_items",
        ("id",),
        "r",
    ),
    (
        "amex_direct_prior_coverages",
        "f",
        ("import_id",),
        "amex_statement_imports",
        ("id",),
        "r",
    ),
    (
        "amex_direct_prior_coverages",
        "f",
        ("imported_expense_id",),
        "expense_reports",
        ("id",),
        "r",
    ),
    (
        "amex_direct_prior_coverages",
        "f",
        ("original_cut_id",),
        "amex_accounting_cuts",
        ("id",),
        "r",
    ),
    (
        "amex_direct_prior_coverages",
        "f",
        ("original_direct_cut_id",),
        "amex_direct_accounting_cuts",
        ("id",),
        "r",
    ),
    (
        "amex_direct_prior_coverages",
        "f",
        ("representation_id",),
        "amex_recognition_representations",
        ("id",),
        "r",
    ),
    ("amex_direct_prior_coverages", "p", ("id",)),
    ("amex_direct_prior_coverages", "u", ("consumption_id",)),
    ("amex_direct_prior_coverages", "u", ("direct_item_id",)),
    ("amex_direct_prior_coverages", "u", ("imported_expense_id",)),
    (
        "amex_direct_recovered_rows",
        "f",
        ("direct_item_id",),
        "amex_direct_items",
        ("id",),
        "r",
    ),
    (
        "amex_direct_recovered_rows",
        "f",
        ("import_item_id",),
        "amex_statement_import_items",
        ("id",),
        "r",
    ),
    (
        "amex_direct_recovered_rows",
        "f",
        ("imported_expense_id",),
        "expense_reports",
        ("id",),
        "r",
    ),
    (
        "amex_direct_recovered_rows",
        "f",
        ("revision_id",),
        "amex_direct_coverage_revisions",
        ("id",),
        "r",
    ),
    ("amex_direct_recovered_rows", "p", ("id",)),
    ("amex_direct_recovered_rows", "u", ("direct_item_id",)),
    ("amex_direct_recovered_rows", "u", ("import_item_id",)),
    ("amex_direct_recovered_rows", "u", ("imported_expense_id",)),
    (
        "amex_expense_account_settlement_allocations",
        "f",
        ("destination_credit_line_id",),
        "accounting_poliza_lines",
        ("id",),
        "r",
    ),
    (
        "amex_expense_account_settlement_allocations",
        "f",
        ("destination_debit_line_id",),
        "accounting_poliza_lines",
        ("id",),
        "r",
    ),
    (
        "amex_expense_account_settlement_allocations",
        "f",
        ("origin_account_id",),
        "cuentas_contables",
        ("id",),
        "r",
    ),
    (
        "amex_expense_account_settlement_allocations",
        "f",
        ("origin_cuenta_gastos_id",),
        "cuentas_de_gastos",
        ("id",),
        "r",
    ),
    (
        "amex_expense_account_settlement_allocations",
        "f",
        ("origin_document_id",),
        "documentos",
        ("id",),
        "r",
    ),
    (
        "amex_expense_account_settlement_allocations",
        "f",
        ("origin_expense_id",),
        "expense_reports",
        ("id",),
        "r",
    ),
    (
        "amex_expense_account_settlement_allocations",
        "f",
        ("origin_line_id",),
        "accounting_poliza_lines",
        ("id",),
        "r",
    ),
    (
        "amex_expense_account_settlement_allocations",
        "f",
        ("origin_poliza_id",),
        "accounting_polizas",
        ("id",),
        "r",
    ),
    (
        "amex_expense_account_settlement_allocations",
        "f",
        ("settlement_id",),
        "amex_expense_account_settlements",
        ("id",),
        "r",
    ),
    ("amex_expense_account_settlement_allocations", "p", ("id",)),
    (
        "amex_expense_account_settlement_allocations",
        "u",
        ("destination_credit_line_id",),
    ),
    (
        "amex_expense_account_settlement_allocations",
        "u",
        ("destination_debit_line_id",),
    ),
    (
        "amex_expense_account_settlements",
        "f",
        ("accounting_poliza_id",),
        "accounting_polizas",
        ("id",),
        "r",
    ),
    ("amex_expense_account_settlements", "f", ("actor_id",), "empleados", ("id",), "r"),
    (
        "amex_expense_account_settlements",
        "f",
        ("card_account_id",),
        "amex_card_accounts",
        ("id",),
        "r",
    ),
    (
        "amex_expense_account_settlements",
        "f",
        ("context_id",),
        "amex_statement_direct_contexts",
        ("id",),
        "r",
    ),
    (
        "amex_expense_account_settlements",
        "f",
        ("cut_id",),
        "amex_direct_accounting_cuts",
        ("id",),
        "r",
    ),
    (
        "amex_expense_account_settlements",
        "f",
        ("direct_item_id",),
        "amex_direct_items",
        ("id",),
        "r",
    ),
    (
        "amex_expense_account_settlements",
        "f",
        ("import_id",),
        "amex_statement_imports",
        ("id",),
        "r",
    ),
    (
        "amex_expense_account_settlements",
        "f",
        ("imported_expense_id",),
        "expense_reports",
        ("id",),
        "r",
    ),
    ("amex_expense_account_settlements", "p", ("id",)),
    ("amex_expense_account_settlements", "u", ("direct_item_id",)),
    ("amex_expense_account_settlements", "u", ("imported_expense_id",)),
    ("amex_merchant_identity_aliases", "f", ("actor_id",), "empleados", ("id",), "r"),
    (
        "amex_merchant_identity_aliases",
        "f",
        ("card_account_id",),
        "amex_card_accounts",
        ("id",),
        "r",
    ),
    ("amex_merchant_identity_aliases", "p", ("id",)),
    (
        "amex_merchant_identity_aliases",
        "u",
        ("entity_code", "card_account_id", "descriptor", "version"),
    ),
    (
        "amex_recognition_consumptions",
        "f",
        ("accounting_poliza_id",),
        "accounting_polizas",
        ("id",),
        "r",
    ),
    ("amex_recognition_consumptions", "f", ("actor_id",), "empleados", ("id",), "r"),
    (
        "amex_recognition_consumptions",
        "f",
        ("card_account_id",),
        "amex_card_accounts",
        ("id",),
        "r",
    ),
    (
        "amex_recognition_consumptions",
        "f",
        ("imported_expense_id",),
        "expense_reports",
        ("id",),
        "r",
    ),
    (
        "amex_recognition_consumptions",
        "f",
        ("liability_account_id",),
        "cuentas_contables",
        ("id",),
        "r",
    ),
    (
        "amex_recognition_consumptions",
        "f",
        ("source_report_expense_id",),
        "expense_reports",
        ("id",),
        "r",
    ),
    ("amex_recognition_consumptions", "p", ("id",)),
    ("amex_recognition_consumptions", "u", ("imported_expense_id",)),
    ("amex_recognition_consumptions", "u", ("source_report_expense_id",)),
    ("amex_statement_direct_contexts", "f", ("actor_id",), "empleados", ("id",), "r"),
    (
        "amex_statement_direct_contexts",
        "f",
        ("import_id",),
        "amex_statement_imports",
        ("id",),
        "r",
    ),
    ("amex_statement_direct_contexts", "p", ("id",)),
    ("amex_statement_direct_contexts", "u", ("import_id", "version")),
    (
        "amex_statement_direct_state",
        "f",
        ("context_id",),
        "amex_statement_direct_contexts",
        ("id",),
        "r",
    ),
    (
        "amex_statement_direct_state",
        "f",
        ("import_id",),
        "amex_statement_imports",
        ("id",),
        "r",
    ),
    ("amex_statement_direct_state", "p", ("import_id",)),
    (
        "amex_statement_import_items",
        "f",
        ("import_id",),
        "amex_statement_imports",
        ("id",),
        "r",
    ),
    (
        "amex_statement_import_items",
        "f",
        ("import_id", "workflow_mode"),
        "amex_statement_imports",
        ("id", "workflow_mode"),
        "r",
    ),
    (
        "amex_statement_import_items",
        "f",
        ("imported_expense_id",),
        "expense_reports",
        ("id",),
        "r",
    ),
    ("amex_statement_import_items", "p", ("id",)),
    ("amex_statement_import_items", "u", ("import_id", "row_number")),
    ("amex_statement_import_items", "u", ("imported_expense_id",)),
    ("amex_statement_imports", "f", ("actor_id",), "empleados", ("id",), "r"),
    (
        "amex_statement_imports",
        "f",
        ("card_account_id",),
        "amex_card_accounts",
        ("id",),
        "r",
    ),
    ("amex_statement_imports", "f", ("employee_id",), "empleados", ("id",), "r"),
    ("amex_statement_imports", "p", ("id",)),
    (
        "amex_statement_imports",
        "u",
        (
            "file_sha256",
            "card_account_id",
            "employee_id",
            "period_start",
            "period_end",
            "parser_version",
        ),
    ),
    ("amex_statement_imports", "u", ("id", "workflow_mode")),
    ("amex_statement_source_files", "f", ("actor_id",), "empleados", ("id",), "r"),
    (
        "amex_statement_source_files",
        "f",
        ("import_id",),
        "amex_statement_imports",
        ("id",),
        "r",
    ),
    ("amex_statement_source_files", "p", ("id",)),
    ("amex_statement_source_files", "u", ("import_id",)),
]

EXPECTED_TRIGGERS = {
    "ad_cfdi_usage_expense": (
        "expense_reports",
        "amex_direct_cfdi_usage_guard",
        23,
        False,
        False,
    ),
    "ad_cfdi_usage_item": (
        "amex_direct_items",
        "amex_direct_cfdi_usage_guard",
        23,
        False,
        False,
    ),
    "ad_cross_owner_informe": (
        "amex_accounting_cuts",
        "amex_direct_cross_owner_guard",
        7,
        False,
        False,
    ),
    "ad_cross_owner_lines": (
        "accounting_poliza_lines",
        "amex_direct_cross_owner_guard",
        7,
        False,
        False,
    ),
    "ad_cut_proof_accounting_poliza_lines": (
        "accounting_poliza_lines",
        "amex_direct_check_cut",
        21,
        True,
        True,
    ),
    "ad_cut_proof_amex_direct_accounting_cuts": (
        "amex_direct_accounting_cuts",
        "amex_direct_check_cut",
        5,
        True,
        True,
    ),
    "ad_cut_proof_amex_direct_cut_items": (
        "amex_direct_cut_items",
        "amex_direct_check_cut",
        5,
        True,
        True,
    ),
    "ad_cut_proof_amex_recognition_consumptions": (
        "amex_recognition_consumptions",
        "amex_direct_check_cut",
        21,
        True,
        True,
    ),
    "ad_frozen_accounting_poliza_lines": (
        "accounting_poliza_lines",
        "amex_direct_frozen_reference_guard",
        27,
        False,
        False,
    ),
    "ad_frozen_accounting_polizas": (
        "accounting_polizas",
        "amex_direct_frozen_reference_guard",
        27,
        False,
        False,
    ),
    "ad_frozen_budget_movement_assignments": (
        "budget_movement_assignments",
        "amex_direct_frozen_reference_guard",
        27,
        False,
        False,
    ),
    "ad_frozen_expense_reports": (
        "expense_reports",
        "amex_direct_frozen_reference_guard",
        27,
        False,
        False,
    ),
    "ad_immutable_amex_cfdi_status_receipts": (
        "amex_cfdi_status_receipts",
        "amex_direct_immutable",
        27,
        False,
        False,
    ),
    "ad_immutable_amex_direct_accounting_cuts": (
        "amex_direct_accounting_cuts",
        "amex_direct_immutable",
        27,
        False,
        False,
    ),
    "ad_immutable_amex_direct_coverage_revisions": (
        "amex_direct_coverage_revisions",
        "amex_direct_immutable",
        27,
        False,
        False,
    ),
    "ad_immutable_amex_direct_cut_items": (
        "amex_direct_cut_items",
        "amex_direct_immutable",
        27,
        False,
        False,
    ),
    "ad_immutable_amex_direct_events": (
        "amex_direct_events",
        "amex_direct_immutable",
        27,
        False,
        False,
    ),
    "ad_immutable_amex_direct_recovered_rows": (
        "amex_direct_recovered_rows",
        "amex_direct_immutable",
        27,
        False,
        False,
    ),
    "ad_immutable_amex_statement_direct_contexts": (
        "amex_statement_direct_contexts",
        "amex_direct_immutable",
        27,
        False,
        False,
    ),
    "ad_immutable_amex_statement_source_files": (
        "amex_statement_source_files",
        "amex_direct_immutable",
        27,
        False,
        False,
    ),
    "ad_projection_amex_direct_items": (
        "amex_direct_items",
        "amex_direct_projection_guard",
        31,
        False,
        False,
    ),
    "ad_projection_amex_statement_direct_state": (
        "amex_statement_direct_state",
        "amex_direct_projection_guard",
        31,
        False,
        False,
    ),
    "ad_recovered_expense_identity": (
        "expense_reports",
        "amex_direct_recovered_expense_guard",
        27,
        False,
        False,
    ),
    "ad_recovery_revision": (
        "amex_direct_coverage_revisions",
        "amex_direct_recovery_guard",
        7,
        False,
        False,
    ),
    "ad_recovery_row": (
        "amex_direct_recovered_rows",
        "amex_direct_recovery_guard",
        7,
        False,
        False,
    ),
    "ad_recovery_seal_revision": (
        "amex_direct_coverage_revisions",
        "amex_direct_recovery_seal",
        5,
        True,
        True,
    ),
    "ad_recovery_seal_row": (
        "amex_direct_recovered_rows",
        "amex_direct_recovery_seal",
        5,
        True,
        True,
    ),
    "aesc_allocation": (
        "amex_expense_account_settlement_allocations",
        "amex_settlement_source_guard",
        7,
        False,
        False,
    ),
    "aesc_allocation_seal": (
        "amex_expense_account_settlement_allocations",
        "amex_settlement_check",
        5,
        True,
        True,
    ),
    "aesc_completion_seal": (
        "amex_direct_completion_receipts",
        "amex_direct_completion_check",
        5,
        True,
        True,
    ),
    "aesc_debtor_new_line": (
        "accounting_poliza_lines",
        "amex_settlement_frozen_guard",
        7,
        False,
        False,
    ),
    "aesc_frozen_amex_direct_coverage_revisions": (
        "amex_direct_coverage_revisions",
        "amex_settlement_frozen_guard",
        31,
        False,
        False,
    ),
    "aesc_frozen_amex_direct_items": (
        "amex_direct_items",
        "amex_settlement_frozen_guard",
        31,
        False,
        False,
    ),
    "aesc_frozen_amex_statement_direct_contexts": (
        "amex_statement_direct_contexts",
        "amex_settlement_frozen_guard",
        31,
        False,
        False,
    ),
    "aesc_frozen_amex_statement_direct_state": (
        "amex_statement_direct_state",
        "amex_settlement_frozen_guard",
        31,
        False,
        False,
    ),
    "aesc_header": (
        "amex_expense_account_settlements",
        "amex_settlement_header_guard",
        7,
        False,
        False,
    ),
    "aesc_immutable_amex_direct_completion_receipts": (
        "amex_direct_completion_receipts",
        "amex_direct_immutable",
        27,
        False,
        False,
    ),
    "aesc_immutable_amex_direct_prior_coverages": (
        "amex_direct_prior_coverages",
        "amex_direct_immutable",
        27,
        False,
        False,
    ),
    "aesc_immutable_amex_expense_account_settlement_allocations": (
        "amex_expense_account_settlement_allocations",
        "amex_direct_immutable",
        27,
        False,
        False,
    ),
    "aesc_immutable_amex_expense_account_settlements": (
        "amex_expense_account_settlements",
        "amex_direct_immutable",
        27,
        False,
        False,
    ),
    "aesc_liquidation_seal": (
        "accounting_poliza_lines",
        "amex_settlement_liquidation_check",
        5,
        True,
        True,
    ),
    "aesc_origin_accounting_poliza_lines": (
        "accounting_poliza_lines",
        "amex_settlement_frozen_guard",
        27,
        False,
        False,
    ),
    "aesc_origin_accounting_polizas": (
        "accounting_polizas",
        "amex_settlement_frozen_guard",
        27,
        False,
        False,
    ),
    "aesc_origin_cfdi_reports": (
        "cfdi_reports",
        "amex_settlement_frozen_guard",
        27,
        False,
        False,
    ),
    "aesc_origin_cuentas_de_gastos": (
        "cuentas_de_gastos",
        "amex_settlement_frozen_guard",
        27,
        False,
        False,
    ),
    "aesc_origin_documentos": (
        "documentos",
        "amex_settlement_frozen_guard",
        27,
        False,
        False,
    ),
    "aesc_origin_expense_reports": (
        "expense_reports",
        "amex_settlement_frozen_guard",
        27,
        False,
        False,
    ),
    "aesc_prior": (
        "amex_direct_prior_coverages",
        "amex_direct_prior_guard",
        7,
        False,
        False,
    ),
    "aesc_prior_seal": (
        "amex_direct_prior_coverages",
        "amex_direct_completion_check",
        5,
        True,
        True,
    ),
    "aesc_settlement_seal": (
        "amex_expense_account_settlements",
        "amex_settlement_check",
        5,
        True,
        True,
    ),
    "amex_approval_proof_guard": (
        "aprobaciones",
        "amex_guard_approval_proof",
        27,
        False,
        False,
    ),
    "amex_bank_association_guard": (
        "amex_report_bank_associations",
        "amex_guard_bank_association",
        31,
        False,
        False,
    ),
    "amex_bank_group_exact": (
        "amex_report_bank_associations",
        "amex_check_bank_group",
        5,
        True,
        True,
    ),
    "amex_bridged_expense_guard": (
        "expense_reports",
        "amex_guard_bridged_expense",
        27,
        False,
        False,
    ),
    "amex_consumption_guard": (
        "amex_recognition_consumptions",
        "amex_guard_consumption",
        31,
        False,
        False,
    ),
    "amex_cut_history_guard": (
        "amex_accounting_cuts",
        "amex_guard_cut_history",
        27,
        False,
        False,
    ),
    "amex_direct_alias_guard": (
        "amex_merchant_identity_aliases",
        "amex_direct_alias_guard",
        31,
        False,
        False,
    ),
    "amex_direct_alias_revision_check": (
        "amex_merchant_identity_aliases",
        "amex_direct_alias_revision_check",
        17,
        True,
        True,
    ),
    "amex_direct_consumption_guard": (
        "amex_recognition_consumptions",
        "amex_direct_consumption_guard",
        31,
        False,
        False,
    ),
    "amex_direct_context_guard": (
        "amex_statement_direct_contexts",
        "amex_direct_context_guard",
        7,
        False,
        False,
    ),
    "amex_direct_cut_insert_guard": (
        "amex_direct_accounting_cuts",
        "amex_direct_cut_insert_guard",
        7,
        False,
        False,
    ),
    "amex_direct_cut_item_guard": (
        "amex_direct_cut_items",
        "amex_direct_cut_item_guard",
        7,
        False,
        False,
    ),
    "amex_direct_event_guard": (
        "amex_direct_events",
        "amex_direct_event_guard",
        7,
        False,
        False,
    ),
    "amex_direct_representation_guard": (
        "amex_recognition_representations",
        "amex_direct_representation_guard",
        31,
        False,
        False,
    ),
    "amex_direct_source_guard": (
        "amex_statement_source_files",
        "amex_direct_source_guard",
        7,
        False,
        False,
    ),
    "amex_report_anchor_consumption": (
        "amex_recognition_consumptions",
        "amex_check_report_anchor",
        21,
        True,
        True,
    ),
    "amex_report_anchor_representation": (
        "amex_recognition_representations",
        "amex_check_report_anchor",
        29,
        True,
        True,
    ),
    "amex_representation_guard": (
        "amex_recognition_representations",
        "amex_guard_representation",
        31,
        False,
        False,
    ),
    "amex_source_evidence_guard": (
        "amex_report_source_evidences",
        "amex_guard_source_evidence",
        31,
        False,
        False,
    ),
    "amex_statement_counts_guard": (
        "amex_statement_imports",
        "amex_statement_counts",
        5,
        True,
        True,
    ),
    "amex_statement_expense_owner_guard": (
        "expense_reports",
        "amex_statement_expense_owner",
        19,
        False,
        False,
    ),
    "amex_statement_import_guard": (
        "amex_statement_imports",
        "amex_statement_immutable",
        27,
        False,
        False,
    ),
    "amex_statement_item_counts_guard": (
        "amex_statement_import_items",
        "amex_statement_counts",
        5,
        True,
        True,
    ),
    "amex_statement_item_guard": (
        "amex_statement_import_items",
        "amex_statement_immutable",
        27,
        False,
        False,
    ),
    "amex_statement_item_owner_guard": (
        "amex_statement_import_items",
        "amex_statement_item_owner",
        7,
        False,
        False,
    ),
    "amex_statement_link_guard": (
        "amex_statement_cut_links",
        "amex_statement_immutable",
        27,
        False,
        False,
    ),
}

EXPECTED_FUNCTION_HASHES = {
    "amex_check_bank_group": "05a1beb987c2f7cefcaf8d17d0a9a7c0dc7062e31f59b12d9d0456dab3a0184d",
    "amex_check_report_anchor": "0277ff5585f8135818a0ac76f030438ebb578a916f51f142f1f520ab553d0d86",
    "amex_direct_alias_guard": "641900057fcea43d19ab630177c3e6ff29d0928c5b63a4559132d2bd0ef8c68d",
    "amex_direct_alias_revision_check": "41e6192896a8ee053b778a252f9e82851954822a15da79d71813b0f43c1a7140",
    "amex_direct_cfdi_usage_guard": "aadcfa56bfbc38bc774ea6cc2c8cefb0ea707154ec97a151d9b4674385c2abfa",
    "amex_direct_check_cut": "616fa51ce7e58bd7747260569f4442c4c19aafdbd5c32b54d8ac745c4dbd244e",
    "amex_direct_completion_check": "505a44d6352bd51362142bd94e98af71040f692fe4234b64b36448afb8cb773e",
    "amex_direct_consumption_guard": "44629a1116f9f3d8b9c81bc8d990f7686e477d657553c1aba0c861eab8f73412",
    "amex_direct_context_guard": "edd8ecae4065e2997c8db7d0fe0026a009f1b68182c6f5be7d312689721bd69f",
    "amex_direct_cross_owner_guard": "21914c9ab6b91482a660a084b102a79e0a128a5b7c020de30695e655d6a91bf8",
    "amex_direct_cut_insert_guard": "4a4816444f797035c3952906f7f39e28814332f02b057a6dbe101568521873d3",
    "amex_direct_cut_item_guard": "ea966cd3717252c663333b697a353aa444311a88193125e04aff495bc2701295",
    "amex_direct_event_guard": "7ecfee81aa04b3ff3513410a5896c1f122e6f1fad4e66e772e810c69162be1ce",
    "amex_direct_frozen_reference_guard": "52ad815e588a628b9e57f1b61126a79dc50f29901a105981a44cf3cfc5ad9946",
    "amex_direct_immutable": "f70e00d6f2465f80e9884c296e885d6a23344632a74a63933ac9b41af2cca8f7",
    "amex_direct_prior_guard": "ce3280351ceec5676a63a0e6cf6acea342efcb5ff1c27db80a4e963ceda0a204",
    "amex_direct_projection_guard": "6765ba536f1dacdd227198bcec87be4c8c6714c65aefaa8c67d8960a42dbf053",
    "amex_direct_recovered_expense_guard": "8c676b15767b2df35d58d9ce782135e562742b2ec84bacb0e15376b2d8b0aa11",
    "amex_direct_recovery_guard": "b3b8e62e3ab1aba682e906d645deefc359581e768c786b0ccfd75e140d040754",
    "amex_direct_recovery_seal": "6b11e1f4881affb83426506235145d181e94a0a4ccdddcd4e23cde67756830b3",
    "amex_direct_representation_guard": "e37f3861a7e5cae509edaf27be8adcaa788dfc116e2cc502956adf1633a2ef1b",
    "amex_direct_source_guard": "3839634b8be7e75e255b2686f87025e90520f6ca58b3e46c9ae2c6d04a0b2d6f",
    "amex_guard_approval_proof": "ce4bd7277b0da44ed0ecc8e999716e16aadd59c56a2f97c71713733d61ab7b08",
    "amex_guard_bank_association": "62f68ab6e33ddab0492ffda125d1d669136fce06b68c5ee28a385939fe61968d",
    "amex_guard_bridged_expense": "d88818cbc328112994e8de93a7596918f81dfc25cec43dab2ecd1392abb6e8af",
    "amex_guard_consumption": "ac6bcabfdbfaf5be4239b6f32f99407d6a66735b72b47c17147672a3771d6916",
    "amex_guard_cut_history": "4c273fd608bd8163593ca14c7b4839a14d9f9969701ddfe2ee89176475a5cfac",
    "amex_guard_representation": "c8551f78ab3ddb940a1507d08ced77ce0d87987219b28ebe7cf15a2a6acefa02",
    "amex_guard_source_evidence": "55a6000a17d9f179e3236be8f8a60d25f2c21038e346f06e6c02ecbea9f1308a",
    "amex_settlement_check": "a8c650eff9a145fd504de4efc346f576e4dd7cf98cc4e58d2eda5c05028f1716",
    "amex_settlement_frozen_guard": "8a594b652ed45089b9056219af0dceca0d2f67539ac8a8d29cd936113751455b",
    "amex_settlement_header_guard": "bba1e28dbf82acc841b289fa794108382269110f06cdd0c54f23e9bd44b101d5",
    "amex_settlement_liquidation_check": "1216442fb5818012c35202505f78c1a5a24173822f8518000357becd48f8848c",
    "amex_settlement_source_guard": "0be7ca58c5678c483c15cda707be4ae19c25b0deaf8472a650979d773f87fc40",
    "amex_statement_counts": "61908a151671fd663d09bb2a6bacfad0e6aee84a80835c8c0a86e173cc9dd4ee",
    "amex_statement_expense_owner": "99ddc5bcd7d09652095cb0b297336d938ce353fe6790931ca1f9d464ad0267a2",
    "amex_statement_immutable": "37a9f7994695c830288a8d9ac1223802d4166be6d317feeee6145b9b10055012",
    "amex_statement_item_owner": "6f331f3aa95bbf32a3fdfe7a994669b8424d2d50eda499f786b5706d39560b05",
}

EXPECTED_INDEXES = {
    "ux_amex_accounting_cuts_initial_informe": (
        "amex_accounting_cuts",
        "CREATE UNIQUE INDEX "
        "ux_amex_accounting_cuts_initial_informe "
        "ON amex_accounting_cuts USING btree "
        "(informe_id) WHERE ((kind)::text = "
        "'initial'::text)",
    ),
    "ux_amex_merchant_active": (
        "amex_merchant_identity_aliases",
        "CREATE UNIQUE INDEX ux_amex_merchant_active ON "
        "amex_merchant_identity_aliases USING btree (entity_code, "
        "card_account_id, descriptor) WHERE active",
    ),
}

EXPECTED_EFFECTIVE_VIEW_HASH = (
    "fc3b15179a2eacd6e82b0944139941ca6d58fcb920195ca0938c4fd3260c9b7b"
)

EXPECTED_EFFECTIVE_VIEW_DEFINITION = (
    " SELECT d.id,\n"
    "    d.import_id,\n"
    "    d.import_item_id,\n"
    "    COALESCE(rr.imported_expense_id, d.imported_expense_id) AS imported_expense_id,\n"
    "    COALESCE(rr.movement_kind, d.movement_kind) AS movement_kind,\n"
    "    COALESCE(rr.raw_signed_amount, d.raw_signed_amount) AS raw_signed_amount,\n"
    "    COALESCE(rr.raw_amount, d.raw_amount) AS raw_amount,\n"
    "    COALESCE(rr.amount_column, d.amount_column) AS amount_column,\n"
    "    COALESCE(rr.profile, d.profile) AS profile,\n"
    "    d.profile_version,\n"
    "    COALESCE(rr.effective_source_json, d.source_json) AS source_json,\n"
    "    d.context_id,\n"
    "    d.context_version,\n"
    "    d.source_version,\n"
    "    d.source_hash,\n"
    "    d.match_status,\n"
    "    d.match_reason,\n"
    "    d.match_proof,\n"
    "    d.cfdi_id,\n"
    "    d.status_receipt_id,\n"
    "    d.merchant_alias_id,\n"
    "    d.declared_uuid,\n"
    "    d.issuer_rfc,\n"
    "    d.tournament_id,\n"
    "    d.project_code,\n"
    "    d.edition_year,\n"
    "    d.budget_concept_id,\n"
    "    d.budget_line_id,\n"
    "    d.budget_version_id,\n"
    "    d.budget_assignment_id,\n"
    "    d.budget_status,\n"
    "    d.accounting_status,\n"
    "    d.credit_excluded,\n"
    "    d.updated_at,\n"
    "    rr.id AS recovery_id,\n"
    "    rr.revision_id AS recovery_revision_id,\n"
    "    rr.proof_hash AS recovery_proof_hash,\n"
    "    COALESCE(cr.period_start, h.period_start) AS period_start,\n"
    "    COALESCE(cr.period_end, h.period_end) AS period_end\n"
    "   FROM amex_direct_items d\n"
    "     JOIN amex_statement_imports h ON h.id = d.import_id\n"
    "     LEFT JOIN amex_direct_recovered_rows rr ON rr.direct_item_id = d.id\n"
    "     LEFT JOIN amex_direct_coverage_revisions cr ON cr.import_id = d.import_id;"
)

EXPECTED_SETTLEMENT_VIEW_HASHES = {
    "amex_direct_prior_sources": "180b7f78707b6fe45bb0fbaa8b19f90dab82bb30a5e3ec63310b5c46f7263c14",
    "amex_expense_account_credit_sources": "444f23fa6faef6285ce7cc60691d752ded74e51ae2026f4a6ae9faa7a54f02c2",
}

EXPECTED_SETTLEMENT_VIEW_DEFINITIONS = {
    "amex_direct_prior_sources": " SELECT rc.id AS consumption_id,\n"
    "    rp.id AS representation_id,\n"
    "    ac.id AS original_cut_id,\n"
    "    rc.accounting_poliza_id AS origin_poliza_id,\n"
    "    l.id AS origin_line_id,\n"
    "    e.id AS origin_expense_id,\n"
    "    d.id AS origin_document_id,\n"
    "    c.id AS origin_cuenta_gastos_id,\n"
    "    l.cuenta_contable_id AS origin_account_id,\n"
    "    l.cuenta_codigo AS origin_account_code,\n"
    "    rc.amount AS credit_amount,\n"
    "    rc.amount AS residual_amount,\n"
    "    upper(cf.receptor_rfc::text) AS receiver_rfc,\n"
    "    rc.currency,\n"
    "    cf.cfdi_uuid,\n"
    "    cf.id AS origin_cfdi_id,\n"
    "    encode(sha256(convert_to(cf.xml_raw, "
    "'UTF8'::name)), 'hex'::text) AS origin_xml_hash,\n"
    "    jsonb_build_object('uuid', cf.cfdi_uuid, 'date', "
    "cf.fecha::text, 'rfc_emisor', cf.emisor_rfc, "
    "'rfc_receptor', cf.receptor_rfc, 'total', "
    "cf.total::text, 'folio', cf.folio, 'nombre_emisor', "
    "cf.emisor_nombre, 'uso_cfdi', cf.receptor_uso_cfdi) AS "
    "origin_cfdi_json,\n"
    "    l.concepto,\n"
    "    rc.card_account_id,\n"
    "    rc.economic_date,\n"
    "    c.torneo_id AS tournament_id,\n"
    "    e.proyecto AS project_code,\n"
    "    EXTRACT(year FROM e.fecha)::integer AS "
    "edition_year,\n"
    "    COALESCE(c.beneficiario_empleado_id, c.empleado_id) "
    "AS employee_id,\n"
    "    emp.nombre AS employee_name,\n"
    "    false AS ambiguous_liquidation,\n"
    "    false AS prior_amex_recognition\n"
    "   FROM amex_recognition_consumptions rc\n"
    "     JOIN amex_recognition_representations rp ON "
    "rp.consumption_id = rc.id AND rp.role::text = "
    "'report'::text\n"
    "     JOIN amex_accounting_cuts ac ON "
    "ac.accounting_poliza_id = rc.accounting_poliza_id AND "
    "(ac.snapshot_json -> 'consumption_ids'::text) ? "
    "rc.id::text\n"
    "     JOIN expense_reports e ON e.id = "
    "rc.source_report_expense_id AND e.id = rp.expense_id\n"
    "     JOIN documentos d ON d.id = ac.informe_id\n"
    "     JOIN cuentas_de_gastos c ON c.id = "
    "e.cuenta_gastos_id\n"
    "     JOIN empleados emp ON emp.id = "
    "COALESCE(c.beneficiario_empleado_id, c.empleado_id)\n"
    "     JOIN cfdi_reports cf ON cf.id = e.cfdi_report_id\n"
    "     JOIN accounting_poliza_lines l ON l.poliza_id = "
    "rc.accounting_poliza_id AND l.cuenta_contable_id = "
    "rc.liability_account_id AND l.cuenta_codigo::text = "
    "rc.liability_code::text AND l.haber::numeric = "
    "rc.amount AND COALESCE(l.debe, 0::double precision) = "
    "0::double precision AND (l.raw_row_json ->> "
    "'expense_id'::text) = e.id::text\n"
    "  WHERE rc.source_kind::text = 'report'::text AND "
    "rc.binding_mode::text = 'report'::text AND "
    "rc.currency::text = 'MXN'::text AND "
    "e.estado_gasto::text = 'activo'::text AND "
    "COALESCE(e.coi_estado, ''::character varying)::text <> "
    "'reversar'::text AND e.pagado_con_amex_empresa IS TRUE "
    "AND e.ultimos_4_digitos::text = ((( SELECT "
    "amex_card_accounts.last4\n"
    "           FROM amex_card_accounts\n"
    "          WHERE amex_card_accounts.id = "
    "rc.card_account_id))::text) AND d.estado::text = "
    "'aprobado'::text AND d.aprobado_en IS NOT NULL AND NOT "
    "(EXISTS ( SELECT 1\n"
    "           FROM amex_direct_prior_coverages pc\n"
    "          WHERE pc.consumption_id = rc.id)) AND NOT "
    "(EXISTS ( SELECT 1\n"
    "           FROM amex_report_bank_associations ba\n"
    "          WHERE ba.report_consumption_id = rc.id));",
    "amex_expense_account_credit_sources": " SELECT p.id AS origin_poliza_id,\n"
    "    l.id AS origin_line_id,\n"
    "    e.id AS origin_expense_id,\n"
    "    d.id AS origin_document_id,\n"
    "    c.id AS origin_cuenta_gastos_id,\n"
    "    a.id AS origin_account_id,\n"
    "    l.cuenta_codigo AS origin_account_code,\n"
    "    l.haber::numeric AS credit_amount,\n"
    "    l.haber::numeric - COALESCE(( SELECT "
    "sum(x.amount) AS sum\n"
    "           FROM "
    "amex_expense_account_settlement_allocations "
    "x\n"
    "          WHERE x.origin_line_id = l.id), "
    "0::numeric) AS residual_amount,\n"
    "    upper(cf.receptor_rfc::text) AS "
    "receiver_rfc,\n"
    "    upper(e.currency::text) AS currency,\n"
    "    cf.cfdi_uuid,\n"
    "    cf.id AS origin_cfdi_id,\n"
    "    encode(sha256(convert_to(cf.xml_raw, "
    "'UTF8'::name)), 'hex'::text) AS "
    "origin_xml_hash,\n"
    "    jsonb_build_object('uuid', cf.cfdi_uuid, "
    "'date', cf.fecha::text, 'rfc_emisor', "
    "cf.emisor_rfc, 'rfc_receptor', "
    "cf.receptor_rfc, 'total', cf.total::text, "
    "'folio', cf.folio, 'nombre_emisor', "
    "cf.emisor_nombre, 'uso_cfdi', "
    "cf.receptor_uso_cfdi) AS origin_cfdi_json,\n"
    "    l.concepto,\n"
    "    COALESCE(c.beneficiario_empleado_id, "
    "c.empleado_id) AS employee_id,\n"
    "    emp.nombre AS employee_name,\n"
    "    c.torneo_id AS tournament_id,\n"
    "    e.proyecto AS project_code,\n"
    "    EXTRACT(year FROM e.fecha)::integer AS "
    "edition_year,\n"
    "    (EXISTS ( SELECT 1\n"
    "           FROM accounting_poliza_lines "
    "other\n"
    "             JOIN accounting_polizas op ON "
    "op.id = other.poliza_id\n"
    "          WHERE other.cuenta_contable_id = "
    "l.cuenta_contable_id AND other.debe > "
    "0::double precision AND (other.raw_row_json "
    "->> 'cuenta_gastos_id'::text) = c.id::text "
    "AND NOT (EXISTS ( SELECT 1\n"
    "                   FROM "
    "amex_expense_account_settlement_allocations "
    "ax\n"
    "                  WHERE "
    "ax.destination_debit_line_id = other.id)) AND "
    "NOT (EXISTS ( SELECT 1\n"
    "                   FROM "
    "amex_expense_account_settlements sh\n"
    "                  WHERE "
    "sh.accounting_poliza_id = other.poliza_id AND "
    "(other.raw_row_json ->> 'expense_id'::text) = "
    "sh.imported_expense_id::text AND "
    "(other.raw_row_json ->> "
    "'origin_line_id'::text) = l.id::text)))) AS "
    "ambiguous_liquidation,\n"
    "    (EXISTS ( SELECT 1\n"
    "           FROM "
    "amex_recognition_representations rp\n"
    "             JOIN "
    "amex_recognition_consumptions rc ON rc.id = "
    "rp.consumption_id\n"
    "          WHERE rp.expense_id = e.id AND "
    "rc.accounting_poliza_id IS NOT NULL)) AS "
    "prior_amex_recognition\n"
    "   FROM accounting_poliza_lines l\n"
    "     JOIN accounting_polizas p ON p.id = "
    "l.poliza_id\n"
    "     JOIN expense_reports e ON e.id::text = "
    "(l.raw_row_json ->> 'expense_id'::text)\n"
    "     JOIN documentos d ON d.id::text = "
    "(l.raw_row_json ->> 'documento_id'::text)\n"
    "     JOIN cuentas_de_gastos c ON c.id = "
    "e.cuenta_gastos_id AND c.id::text = "
    "(l.raw_row_json ->> "
    "'cuenta_gastos_id'::text)\n"
    "     JOIN cuentas_contables a ON a.id = "
    "l.cuenta_contable_id AND a.codigo::text = "
    "l.cuenta_codigo::text\n"
    "     JOIN empleados emp ON emp.id = "
    "COALESCE(c.beneficiario_empleado_id, "
    "c.empleado_id)\n"
    "     JOIN cfdi_reports cf ON cf.id = "
    "e.cfdi_report_id\n"
    "  WHERE p.origen::text = "
    "'deudores_comprobacion'::text AND "
    "d.tipo::text = 'INFORME'::text AND "
    "d.estado::text = 'aprobado'::text AND "
    "d.aprobado_en IS NOT NULL AND "
    "(d.cuenta_gastos_id = c.id OR (EXISTS ( "
    "SELECT 1\n"
    "           FROM documentos parent\n"
    "          WHERE parent.id = "
    "d.informe_origen_id AND "
    "parent.cuenta_gastos_id = c.id))) AND "
    "(e.informe_documento_id IS NULL OR "
    "e.informe_documento_id = d.id) AND "
    "e.estado_gasto::text = 'activo'::text AND "
    "COALESCE(e.coi_estado, ''::character "
    "varying)::text <> 'reversar'::text AND "
    "COALESCE(l.debe, 0::double precision) = "
    "0::double precision AND l.haber > 0::double "
    "precision AND l.haber::numeric = "
    "round(l.haber::numeric, 2) AND "
    "(l.raw_row_json ->> 'movement'::text) = "
    "'haber_contrapartida'::text AND "
    "(a.codigo::text ~~ '1170-001-%'::text OR "
    "a.codigo::text ~~ '1170-002-%'::text) AND "
    "a.activo IS TRUE AND cf.xml_raw IS NOT NULL "
    "AND upper(e.currency::text) = 'MXN'::text;",
}

# PostgreSQL omits these exact casts for the three legacy TEXT columns. The
# complete predicates and every other cast remain byte-exact after whitespace.
# These two finite forms do not relax CHECKs, owner guards or arbitrary SQL.
EXPECTED_SETTLEMENT_TEXT_VIEW_DEFINITIONS = {
    name: definition.replace("d.tipo::text", "d.tipo")
    .replace("d.estado::text", "d.estado")
    .replace("e.estado_gasto::text", "e.estado_gasto")
    for name, definition in EXPECTED_SETTLEMENT_VIEW_DEFINITIONS.items()
}

EXPECTED_SETTLEMENT_TEXT_VIEW_HASHES = {
    "amex_direct_prior_sources": (
        "7efaf55b6a54d44fd67b7eb2b8496e6a" "72f2cad18022a8b9bcf4c82200bf3ad0"
    ),
    "amex_expense_account_credit_sources": (
        "6eb86b964c72c0f2d83a27699e87367a" "f9adfe701078220bfdd1a8f555de85ba"
    ),
}


# Exact metadata captured from disposable PostgreSQL 16 after 20261021 owner DDL.
EXPECTED_CHECKS.update(
    {
        (
            "amex_direct_no_cfdi_decisions",
            "amex_direct_no_cfdi_decisions_revision_check",
        ): "CHECK ((revision > 0))",
        (
            "amex_direct_no_cfdi_decisions",
            "amex_direct_no_cfdi_decisions_action_check",
        ): (
            "CHECK (((action)::text = ANY ((ARRAY['de"
            "clare'::character varying, 'revoke'::cha"
            "racter varying])::text[])))"
        ),
        (
            "amex_direct_no_cfdi_decisions",
            "amex_direct_no_cfdi_decisions_treatment_check",
        ): "CHECK (((treatment)::text = 'full_amount_expense_no_tax'::text))",
        (
            "amex_direct_no_cfdi_decisions",
            "amex_direct_no_cfdi_decisions_reason_check",
        ): (
            "CHECK (((length(TRIM(BOTH FROM reason)) "
            ">= 1) AND (length(TRIM(BOTH FROM reason)"
            ") <= 1000)))"
        ),
        (
            "amex_direct_no_cfdi_decisions",
            "amex_direct_no_cfdi_decisions_payload_hash_check",
        ): "CHECK ((length((payload_hash)::text) = 64))",
        (
            "amex_direct_no_cfdi_decisions",
            "amex_direct_no_cfdi_decisions_economic_source_hash_check",
        ): "CHECK ((length((economic_source_hash)::text) = 64))",
        (
            "amex_direct_no_cfdi_decisions",
            "amex_direct_no_cfdi_decisions_idempotency_key_check",
        ): "CHECK ((length((idempotency_key)::text) = 64))",
        (
            "amex_direct_no_cfdi_decisions",
            "amex_direct_no_cfdi_decisions_request_id_check",
        ): (
            "CHECK (((length(TRIM(BOTH FROM request_i"
            "d)) >= 1) AND (length(TRIM(BOTH FROM req"
            "uest_id)) <= 160)))"
        ),
    }
)
EXPECTED_KEYS.extend(
    [
        ("amex_direct_no_cfdi_decisions", "p", ("id",)),
        ("amex_direct_no_cfdi_decisions", "u", ("item_id", "revision")),
        ("amex_direct_no_cfdi_decisions", "u", ("item_id", "idempotency_key")),
        ("amex_direct_no_cfdi_decisions", "u", ("previous_decision_id",)),
        (
            "amex_direct_no_cfdi_decisions",
            "f",
            ("item_id",),
            "amex_direct_items",
            ("id",),
            "r",
        ),
        (
            "amex_direct_no_cfdi_decisions",
            "f",
            ("import_id",),
            "amex_statement_imports",
            ("id",),
            "r",
        ),
        (
            "amex_direct_no_cfdi_decisions",
            "f",
            ("imported_expense_id",),
            "expense_reports",
            ("id",),
            "r",
        ),
        (
            "amex_direct_no_cfdi_decisions",
            "f",
            ("context_id",),
            "amex_statement_direct_contexts",
            ("id",),
            "r",
        ),
        (
            "amex_direct_no_cfdi_decisions",
            "f",
            ("previous_decision_id",),
            "amex_direct_no_cfdi_decisions",
            ("id",),
            "r",
        ),
        (
            "amex_direct_no_cfdi_decisions",
            "f",
            ("actor_id",),
            "empleados",
            ("id",),
            "r",
        ),
        (
            "amex_direct_items",
            "f",
            ("no_cfdi_decision_id",),
            "amex_direct_no_cfdi_decisions",
            ("id",),
            "r",
        ),
        (
            "amex_direct_cut_items",
            "f",
            ("no_cfdi_decision_id",),
            "amex_direct_no_cfdi_decisions",
            ("id",),
            "r",
        ),
    ]
)
EXPECTED_TRIGGERS.update(
    {
        "ad_no_cfdi_insert": (
            "amex_direct_no_cfdi_decisions",
            "amex_direct_no_cfdi_insert_guard",
            7,
            False,
            False,
        ),
        "ad_no_cfdi_immutable": (
            "amex_direct_no_cfdi_decisions",
            "amex_direct_immutable",
            27,
            False,
            False,
        ),
        "ad_no_cfdi_decision_seal": (
            "amex_direct_no_cfdi_decisions",
            "amex_direct_no_cfdi_seal",
            5,
            True,
            True,
        ),
        "ad_no_cfdi_event_seal": (
            "amex_direct_events",
            "amex_direct_no_cfdi_seal",
            5,
            True,
            True,
        ),
        "ad_no_cfdi_item_seal": (
            "amex_direct_items",
            "amex_direct_no_cfdi_seal",
            21,
            True,
            True,
        ),
        "ad_no_cfdi_expense": (
            "expense_reports",
            "amex_direct_no_cfdi_expense_guard",
            27,
            False,
            False,
        ),
        "ad_no_cfdi_context": (
            "amex_statement_direct_contexts",
            "amex_direct_no_cfdi_context_guard",
            7,
            False,
            False,
        ),
        "ad_no_cfdi_state": (
            "amex_statement_direct_state",
            "amex_direct_no_cfdi_context_guard",
            19,
            False,
            False,
        ),
        "ad_no_cfdi_recovery": (
            "amex_direct_coverage_revisions",
            "amex_direct_no_cfdi_context_guard",
            7,
            False,
            False,
        ),
    }
)
EXPECTED_FUNCTION_HASHES.update(
    {
        "amex_direct_check_cut": (
            "001c2b079aebb9edeadbaae142fbecaa2f966824" "c53e1e4b2fbafee7d8fe26cc"
        ),
        "amex_direct_cut_item_guard": (
            "d4bd5cb15afa3a5e9745aeae238bb96f900de1c1" "9f931d18c733968f3b9c9f13"
        ),
        "amex_direct_no_cfdi_context_guard": (
            "a35d9b16d2c25192465f6310cc1e013636aa0f06" "419614c1fed3bfb0b8157276"
        ),
        "amex_direct_no_cfdi_expense_guard": (
            "8372cbf9fe6aa04f27728f653d2ea5a92989f70f" "5aa9dd7378056ca52699fa79"
        ),
        "amex_direct_no_cfdi_insert_guard": (
            "3b6f68afc65f0ed7c191b266b25c08966df87f27" "3baa75470b5228566f7d3158"
        ),
        "amex_direct_no_cfdi_seal": (
            "9dc1c73243da3ccc1b560f02d920454293361e05" "f6a7697f89efeed7ef92ba01"
        ),
        "amex_direct_projection_guard": (
            "53a9628c4e88d5772bdd20b08af8175a02f6e63a" "beaf92ad417a279f49003b22"
        ),
    }
)
EXPECTED_NO_CFDI_HELPER_HASHES = {
    "amex_direct_no_cfdi_assert": (
        "fe2babdac8ff79451ddd8087675b1e05b7787036" "e1dac883e19bcd3ceeaa2d70"
    ),
    "amex_direct_no_cfdi_source_hash": (
        "ff404f25fbe1c3c8813962cd2b9145276715f5af" "2dbbffc3ba1f14f8e3fec737"
    ),
}
