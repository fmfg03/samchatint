from samchat.assistant.tournament_registration_reports import (
    build_registration_executive_reports,
)


def test_registration_executive_reports_counts_age_ranges_and_municipal_coverage():
    dataset = {
        "tournaments": [
            {"id": "t1", "name": "Copa Telmex", "slug": "copa-telmex"}
        ],
        "categories": [
            {"id": "cat-f", "name": "Femenil", "branch": "femenil"},
            {"id": "cat-j", "name": "Juvenil 15-17", "branch": "juvenil"},
            {"id": "cat-v", "name": "Varonil", "branch": "varonil"},
        ],
        "teams": [
            {
                "id": "team-f",
                "team_name": "Femenil Uno",
                "state": "Jalisco",
                "municipality": "Guadalajara",
            },
            {
                "id": "team-j",
                "team_name": "Juvenil Uno",
                "state": "Jalisco",
                "municipality": "Zapopan",
            },
            {
                "id": "team-v",
                "team_name": "Varonil Uno",
                "state": "Nuevo León",
                "municipality": "Monterrey",
            },
        ],
        "registrations": [
            {"id": "reg-f", "team_id": "team-f", "category_id": "cat-f"},
            {"id": "reg-j", "team_id": "team-j", "category_id": "cat-j"},
            {"id": "reg-v", "team_id": "team-v", "category_id": "cat-v"},
        ],
        "players": [
            {
                "id": "pf-17",
                "registration_id": "reg-f",
                "birth_date": "2009-01-01",
                "curp": "AAAA090101MJCLRL09",
            },
            {
                "id": "pf-18",
                "registration_id": "reg-f",
                "birth_date": "2008-01-01",
                "curp": "",
            },
            {
                "id": "pj-15",
                "registration_id": "reg-j",
                "birth_date": "2011-01-01",
                "curp": "BBBB110101HJCLRL05",
            },
            {
                "id": "pj-16",
                "registration_id": "reg-j",
                "birth_date": "2010-01-01",
                "curp": "BAD",
            },
            {
                "id": "pj-17",
                "registration_id": "reg-j",
                "birth_date": "2009-01-01",
                "curp": "CCCC090101HJCLRL07",
            },
            {
                "id": "pv-18",
                "registration_id": "reg-v",
                "birth_date": "2008-01-01",
                "curp": "DDDD080101HNLLRL08",
            },
            {
                "id": "pv-24",
                "registration_id": "reg-v",
                "birth_date": "2002-01-01",
                "curp": "EEEE020101HNLLRL05",
            },
            {
                "id": "pv-29",
                "registration_id": "reg-v",
                "birth_date": "1997-01-01",
                "curp": "FFFF970101HNLLRL09",
            },
            {
                "id": "pv-30",
                "registration_id": "reg-v",
                "birth_date": "1996-01-01",
                "curp": "GGGG960101HNLLRL02",
            },
            {
                "id": "pv-missing",
                "registration_id": "reg-v",
                "birth_date": None,
                "curp": None,
            },
        ],
    }

    result = build_registration_executive_reports(
        dataset=dataset,
        tournament_key="copa-telmex",
        tournament_slug="copa-telmex",
        as_of_date="2026-01-01",
        municipality_denominators={
            "source": "test",
            "national_total": 2478,
            "states": {"jalisco": 125, "nuevo leon": 51},
        },
    )

    assert result["summary"] == {
        "equipos": 3,
        "jugadores": 10,
        "estados": 2,
        "municipios_participantes": 3,
    }
    assert {
        "nivel": "estatal",
        "estado": "Jalisco",
        "municipios_participantes": 2,
        "municipios_totales": 125,
        "porcentaje": 1.6,
        "fuente": "test",
    } in result["reports"]["cobertura_municipal"]
    assert result["reports"]["femenil_edad"] == [
        {
            "estado": "Jalisco",
            "municipio": "Guadalajara",
            "menores_17": 1,
            "mayores_18": 1,
            "sin_fecha_nacimiento": 0,
            "fuente": "supabase_tournaments_v2",
        }
    ]
    assert result["reports"]["juvenil_edades"][0]["edad_15"] == 1
    assert result["reports"]["juvenil_edades"][0]["edad_16"] == 1
    assert result["reports"]["juvenil_edades"][0]["edad_17"] == 1
    varonil = result["reports"]["varonil_rangos"][0]
    assert varonil["edad_18"] == 1
    assert varonil["edad_19_24"] == 1
    assert varonil["edad_25_29"] == 1
    assert varonil["edad_30_mas"] == 1
    assert varonil["sin_fecha_nacimiento"] == 1
    quality = result["reports"]["calidad_datos"]
    assert sum(row["curp_faltante"] for row in quality) == 2
    assert sum(row["curp_invalida"] for row in quality) == 1
    validations = result["reports"]["validaciones_externas"]
    assert {row["fmf_status"] for row in validations} == {"unavailable"}
    assert {row["renapo_status"] for row in validations} == {"unavailable"}
