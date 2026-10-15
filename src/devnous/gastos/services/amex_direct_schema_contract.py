"""Static schema receipt from isolated PostgreSQL 16 owner migration tests.

Reviewed with the SQL source; never learned from production catalogs.
Changing an owner guard requires regenerated synthetic evidence and review.
"""

EXPECTED_CHECKS = {
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
    "amex_check_bank_group": (
        "05a1beb987c2f7cefcaf8d17d0a9a7c0" "dc7062e31f59b12d9d0456dab3a0184d"
    ),
    "amex_check_report_anchor": (
        "0277ff5585f8135818a0ac76f030438e" "bb578a916f51f142f1f520ab553d0d86"
    ),
    "amex_direct_alias_guard": (
        "641900057fcea43d19ab630177c3e6ff" "29d0928c5b63a4559132d2bd0ef8c68d"
    ),
    "amex_direct_alias_revision_check": (
        "41e6192896a8ee053b778a252f9e8285" "1954822a15da79d71813b0f43c1a7140"
    ),
    "amex_direct_cfdi_usage_guard": (
        "780c54824b031f94b33510a62f123887" "6774d623f24e425a618dc5f9af35dce9"
    ),
    "amex_direct_check_cut": (
        "2413b680fd115158b48dcd140af3f543" "0bbe9f419d6e68fecbe591784e5e7860"
    ),
    "amex_direct_consumption_guard": (
        "d461dcb36c1ce4d79cd7827fe8d1ff33" "49197e6fa2d65c156024ecddcbc01fd6"
    ),
    "amex_direct_context_guard": (
        "edd8ecae4065e2997c8db7d0fe0026a0" "09f1b68182c6f5be7d312689721bd69f"
    ),
    "amex_direct_cross_owner_guard": (
        "21914c9ab6b91482a660a084b102a79e" "0a128a5b7c020de30695e655d6a91bf8"
    ),
    "amex_direct_cut_insert_guard": (
        "1180c7ac44798ad902dd547fad202ddb" "30058bc06367af9f9405d9e2ec99e99d"
    ),
    "amex_direct_cut_item_guard": (
        "7b2a873cc3659a4a76f550852852d9ae" "924437a43fb010d9263200d12aced98f"
    ),
    "amex_direct_event_guard": (
        "7ecfee81aa04b3ff3513410a5896c1f1" "22e6f1fad4e66e772e810c69162be1ce"
    ),
    "amex_direct_frozen_reference_guard": (
        "52ad815e588a628b9e57f1b61126a79d" "c50f29901a105981a44cf3cfc5ad9946"
    ),
    "amex_direct_immutable": (
        "f70e00d6f2465f80e9884c296e885d6a" "23344632a74a63933ac9b41af2cca8f7"
    ),
    "amex_direct_projection_guard": (
        "515bc3e8f2bfc1d6183b570a15ff49e4" "873df923f3454e6fbb1090b70323b733"
    ),
    "amex_direct_representation_guard": (
        "e37f3861a7e5cae509edaf27be8adcaa" "788dfc116e2cc502956adf1633a2ef1b"
    ),
    "amex_direct_source_guard": (
        "3839634b8be7e75e255b2686f87025e9" "0520f6ca58b3e46c9ae2c6d04a0b2d6f"
    ),
    "amex_guard_approval_proof": (
        "ce4bd7277b0da44ed0ecc8e999716e16" "aadd59c56a2f97c71713733d61ab7b08"
    ),
    "amex_guard_bank_association": (
        "62f68ab6e33ddab0492ffda125d1d669" "136fce06b68c5ee28a385939fe61968d"
    ),
    "amex_guard_bridged_expense": (
        "d88818cbc328112994e8de93a7596918" "f81dfc25cec43dab2ecd1392abb6e8af"
    ),
    "amex_guard_consumption": (
        "ac6bcabfdbfaf5be4239b6f32f99407d" "6a66735b72b47c17147672a3771d6916"
    ),
    "amex_guard_cut_history": (
        "4c273fd608bd8163593ca14c7b4839a1" "4d9f9969701ddfe2ee89176475a5cfac"
    ),
    "amex_guard_representation": (
        "c8551f78ab3ddb940a1507d08ced77ce" "0d87987219b28ebe7cf15a2a6acefa02"
    ),
    "amex_guard_source_evidence": (
        "55a6000a17d9f179e3236be8f8a60d25" "f2c21038e346f06e6c02ecbea9f1308a"
    ),
    "amex_statement_counts": (
        "61908a151671fd663d09bb2a6bacfad0" "e6aee84a80835c8c0a86e173cc9dd4ee"
    ),
    "amex_statement_expense_owner": (
        "99ddc5bcd7d09652095cb0b297336d93" "8ce353fe6790931ca1f9d464ad0267a2"
    ),
    "amex_statement_immutable": (
        "37a9f7994695c830288a8d9ac1223802" "d4166be6d317feeee6145b9b10055012"
    ),
    "amex_statement_item_owner": (
        "6f331f3aa95bbf32a3fdfe7a994669b8" "424d2d50eda499f786b5706d39560b05"
    ),
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
