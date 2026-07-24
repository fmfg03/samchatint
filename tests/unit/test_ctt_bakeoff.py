from scripts.evaluate_ctt_ocr_bakeoff import score


def test_bakeoff_detects_exact_sixteen_without_empty_tail():
    truth = {
        "team": {"name": "Deportivo Estrellas"},
        "players": [
            {"slot": slot, "name": f"Jugadora {slot}", "birth_date": "01/01/2004"}
            for slot in range(1, 17)
        ],
    }
    candidate = {
        "team": {"name": "Deportivo Estrellas"},
        "players": [
            {
                "visible_player_number": slot,
                "name": f"Jugadora {slot}",
                "birth_date": "01/01/2004",
            }
            for slot in range(1, 17)
        ],
    }
    receipt = score(truth, candidate)
    assert receipt["team_exact"] is True
    assert receipt["exact_name_rate"] == 1.0
    assert receipt["exact_birth_date_rate"] == 1.0
    assert receipt["page_exact_counts"]["front"] == {
        "expected": 8,
        "names": 8,
        "dates": 8,
    }
    assert receipt["page_exact_counts"]["back"] == {
        "expected": 8,
        "names": 8,
        "dates": 8,
    }
    assert receipt["acceptance"]["slots_17_20_not_materialized"] is True


def test_bakeoff_reports_invented_slot_seventeen():
    truth = {"team_name": "Equipo", "players": [{"slot": 1, "name": "Ana"}]}
    candidate = {
        "team_name": "Equipo",
        "players": [
            {"visible_player_number": 1, "name": "Ana"},
            {"visible_player_number": 17, "name": "Inventada"},
        ],
    }
    receipt = score(truth, candidate)
    assert receipt["invented_slots"] == [17]
    assert receipt["empty_tail_materialized"] == [17]
    assert receipt["acceptance"]["zero_invented_players"] is False


def test_bakeoff_reads_canonical_review_slots_as_provisional_reference():
    field = lambda name, value: {  # noqa: E731
        "field_name": name,
        "normalized_value": value,
        "raw_text": value,
    }
    truth = {
        "team": {"fields": {"name": field("name", "Deportivo Estrellas")}},
        "slots": [
            {
                "slot": 1,
                "occupied": True,
                "fields": {
                    "given_names": field("given_names", "Sophia"),
                    "paternal_surname": field("paternal_surname", "Rodriguez"),
                    "maternal_surname": field("maternal_surname", "Linares"),
                    "birth_date": field("birth_date", "08/10/2004"),
                },
            },
            {"slot": 2, "occupied": False, "fields": {}},
        ],
    }
    candidate = {
        "team": {"name": "Deportivo Estrellas"},
        "players": [
            {
                "slot": 1,
                "name": "Sophia Rodriguez Linares",
                "birth_date": "08/10/2004",
            }
        ],
    }

    receipt = score(truth, candidate)

    assert receipt["expected_player_count"] == 1
    assert receipt["exact_name_count"] == 1
    assert receipt["exact_birth_date_count"] == 1


def test_bakeoff_compares_two_digit_and_four_digit_dates_semantically():
    truth = {"players": [{"slot": 1, "name": "Ana", "birth_date": "08/10/04"}]}
    candidate = {"players": [{"slot": 1, "name": "Ana", "birth_date": "08/10/2004"}]}

    assert score(truth, candidate)["exact_birth_date_count"] == 1


def test_bakeoff_reports_duplicate_player_identity():
    truth = {
        "players": [
            {"slot": 1, "name": "Axel Soto", "birth_date": "18/08/2011"},
            {"slot": 6, "name": "Axel Soto", "birth_date": "18/08/2011"},
        ]
    }

    receipt = score(truth, truth)

    assert receipt["duplicate_identity_groups"] == [[1, 6]]
    assert receipt["acceptance"]["zero_duplicate_identities"] is False


def test_bakeoff_reports_possible_duplicate_after_ocr_name_drift():
    candidate = {
        "players": [
            {
                "slot": 1,
                "name": "Axel Antonio Soto Ramirez",
                "birth_date": "18/08/2011",
            },
            {
                "slot": 6,
                "name": "Axel Antonio Soto Ruviera",
                "birth_date": "18/08/2011",
            },
        ]
    }

    receipt = score(candidate, candidate)

    assert receipt["possible_duplicate_identity_groups"] == [[1, 6]]
    assert receipt["acceptance"]["zero_duplicate_identities"] is False
