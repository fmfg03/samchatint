import scripts.local_registration_ocr as local_registration_ocr

from devnous.agents.ocr_schemas import RegistrationFormExtraction
from devnous.document_parsing import MinerUParseResult
from devnous.document_parsing.registration_adjudicator import RegistrationAdjudicationResult
from PIL import Image


class _FakeQianfanLayoutHelper:
    def query_layout(self, image):
        return {
            "document_type": "team_roster",
            "page_confidence": 0.72,
            "header": {
                "bbox": {"x": 0, "y": 0, "width": 1000, "height": 260},
                "fields": {
                    "team_name": {"bbox": {"x": 60, "y": 40, "width": 280, "height": 45}},
                    "category": {"bbox": {"x": 360, "y": 40, "width": 120, "height": 45}},
                    "state": {"bbox": {"x": 500, "y": 40, "width": 180, "height": 45}},
                    "manager_name": {"bbox": {"x": 700, "y": 40, "width": 220, "height": 45}},
                },
            },
            "players": [
                {
                    "row_index": 1,
                    "row_bbox": {"x": 40, "y": 260, "width": 920, "height": 120},
                    "name_bbox": {"x": 180, "y": 275, "width": 280, "height": 30},
                    "birth_date_bbox": {"x": 480, "y": 275, "width": 140, "height": 30},
                    "curp_bbox": {"x": 640, "y": 275, "width": 250, "height": 30},
                    "photo_bbox": {"x": 60, "y": 260, "width": 90, "height": 110},
                    "confidence": 0.83,
                }
            ],
        }


class _FakeTrOCRHelper:
    def __init__(self):
        self._results = iter(
            [
                local_registration_ocr.OCRTextResult("Club Deportivo Norte", 0.91),
                local_registration_ocr.OCRTextResult("Sub-15", 0.88),
                local_registration_ocr.OCRTextResult("Jalisco", 0.87),
                local_registration_ocr.OCRTextResult("Luis Garcia", 0.89),
                local_registration_ocr.OCRTextResult("", 0.0),
                local_registration_ocr.OCRTextResult("Juan Perez Lopez", 0.94),
                local_registration_ocr.OCRTextResult("01/02/2011", 0.93),
                local_registration_ocr.OCRTextResult("PELJ110201HJCRPN09", 0.96),
                local_registration_ocr.OCRTextResult("", 0.0),
            ]
        )

    def read(self, image):
        return next(self._results)


class _FakeTemplateAligner:
    def __init__(self, *, side, fields):
        self.side = side
        self.fields = fields
        self.status = local_registration_ocr.ModelLoadStatus(
            model_name="ctt_template_extractor",
            available=True,
            source="test",
        )

    def process_image(self, image, *, out_dir, prefix):
        return {
            "side": self.side,
            "align_info": {"method": "test", "template": self.side},
            "degraded_mode": False,
            "fields": self.fields,
        }


def test_qianfan_layout_regions_feed_current_local_ocr(monkeypatch):
    monkeypatch.setenv("LOCAL_OCR_USE_TEMPLATE_ALIGNER", "0")
    monkeypatch.setenv("LOCAL_OCR_LAYOUT_PROVIDER", "qianfan")
    monkeypatch.setenv("LOCAL_OCR_USE_MOONDREAM", "0")
    monkeypatch.setenv("LOCAL_OCR_USE_TROCR", "1")
    monkeypatch.setattr(local_registration_ocr, "QianfanLayoutHelper", _FakeQianfanLayoutHelper)
    monkeypatch.setattr(local_registration_ocr, "TrOCRHelper", _FakeTrOCRHelper)

    extractor = local_registration_ocr.LocalRegistrationOCR()
    payload = extractor.extract(Image.new("RGB", (1000, 800), "white"))
    extraction = payload["extraction"]

    assert extraction["team"]["name"] == "Club Deportivo Norte"
    assert extraction["team"]["category"] == "Sub-15"
    assert extraction["team"]["state"] == "Jalisco"
    assert extraction["manager"]["name"] == "Luis Garcia"

    assert len(extraction["players"]) == 1
    player = extraction["players"][0]
    assert player["name"] == "Juan Perez Lopez"
    assert player["birth_date"] == "01/02/2011"
    assert player["curp"] == "PELJ110201HJCRPN09"
    assert player["photo_region"] == {
        "x": 60,
        "y": 208,
        "width": 90,
        "height": 88,
        "confidence": 0.0,
    }
    assert player["needs_review"] is True
    assert "curp_invalido" in player["integrity_reasons"]
    assert payload["raw"]["backend"]["layout_provider"] == "qianfan"
    assert "layout=qianfan" in extraction["notes"]


def test_ctt_template_front_extracts_header_and_optional_auxiliar(monkeypatch):
    monkeypatch.setenv("LOCAL_OCR_USE_TEMPLATE_ALIGNER", "0")
    extractor = local_registration_ocr.LocalRegistrationOCR()
    extractor.trocr = object()
    extractor.template_aligner = _FakeTemplateAligner(
        side="front",
        fields={
            "header.equipo_nombre": {
                "text": "Club Deportivo Norte",
                "confidence": 0.91,
            },
            "header.rama": {"text": "Varonil", "confidence": 0.89},
            "header.categoria": {"text": "Sub-15", "confidence": 0.90},
            "header.representante_nombre": {
                "text": "Luis Garcia",
                "confidence": 0.88,
            },
            "header.liga": {"text": "Liga Municipal", "confidence": 0.86},
            "header.correo": {"text": "luis@example.com", "confidence": 0.84},
            "header.estado": {"text": "Jalisco", "confidence": 0.87},
            "header.municipio": {"text": "Zapopan", "confidence": 0.87},
            "cards.director_tecnico.nombre": {
                "text": "Carlos",
                "confidence": 0.88,
            },
            "cards.director_tecnico.apellidos": {
                "text": "Ramirez Lopez",
                "confidence": 0.88,
            },
            "cards.director_tecnico.nacimiento": {
                "text": "03/04/1980",
                "confidence": 0.89,
            },
            "cards.director_tecnico.curp": {
                "text": "RALC800403HJCMPR08",
                "confidence": 0.90,
            },
            "cards.auxiliar.nombre": {"text": "", "confidence": 0.0},
            "cards.auxiliar.apellidos": {"text": "", "confidence": 0.0},
            "cards.auxiliar.nacimiento": {"text": "", "confidence": 0.0},
            "cards.auxiliar.curp": {"text": "", "confidence": 0.0},
            "cards.jugador_1.nombre": {"text": "Juan", "confidence": 0.92},
            "cards.jugador_1.apellidos": {
                "text": "Perez Lopez",
                "confidence": 0.92,
            },
            "cards.jugador_1.nacimiento": {
                "text": "01/02/2011",
                "confidence": 0.93,
            },
            "cards.jugador_1.curp": {
                "text": "PELJ110201HJCRPN09",
                "confidence": 0.94,
            },
        },
    )

    monkeypatch.setattr(
        extractor,
        "_read_template_field",
        lambda field_info: local_registration_ocr.OCRTextResult(
            field_info.get("text", "") if isinstance(field_info, dict) else "",
            field_info.get("confidence", 0.0) if isinstance(field_info, dict) else 0.0,
        ),
    )

    raw = {}
    extraction = extractor._extract_with_template_alignment(
        Image.new("RGB", (1000, 800), "white"),
        raw,
    )

    assert extraction["form_type"] == "copa_telmex_telcel_futbol_2026"
    assert extraction["team"]["name"] == "Club Deportivo Norte"
    assert extraction["team"]["category"] == "Sub-15"
    assert extraction["team"]["gender"] == "Varonil"
    assert extraction["team"]["league"] == "Liga Municipal"
    assert extraction["team"]["municipality"] == "Zapopan"
    assert extraction["team"]["state"] == "Jalisco"
    assert round(extraction["team"]["confidence"], 2) == 0.88
    assert extraction["manager"]["name"] == "Luis Garcia"
    assert extraction["manager"]["email"] == "luis@example.com"
    assert len(extraction["responsables"]) == 1
    assert extraction["responsables"][0]["role"] == "director_tecnico"
    assert extraction["responsables"][0]["name"] == "Carlos Ramirez Lopez"
    assert extraction["responsables"][0]["birth_date"] == "03/04/1980"
    assert extraction["responsables"][0]["curp"] == "RALC800403HJCMPR08"
    assert len(extraction["players"]) == 1
    assert extraction["players"][0]["visible_player_number"] == 1
    assert extraction["players"][0]["continuous_player_number"] == 1
    assert raw["ctt_template"]["sport"] == "futbol"
    assert raw["ctt_template"]["scope"] == "ctt_futbol_only"

    validated = RegistrationFormExtraction.model_validate(extraction)
    assert validated.responsables[0].curp == "RALC800403HJCMPR08"


def test_ctt_template_back_is_repeatable_roster_page(monkeypatch):
    monkeypatch.setenv("LOCAL_OCR_USE_TEMPLATE_ALIGNER", "0")
    extractor = local_registration_ocr.LocalRegistrationOCR()
    extractor.trocr = object()
    extractor.template_aligner = _FakeTemplateAligner(
        side="back",
        fields={
            "cards.jugador_9.nombre": {"text": "Maria", "confidence": 0.91},
            "cards.jugador_9.apellidos": {
                "text": "Santos Ruiz",
                "confidence": 0.91,
            },
            "cards.jugador_9.nacimiento": {
                "text": "05/06/2012",
                "confidence": 0.92,
            },
            "cards.jugador_9.curp": {
                "text": "SARM120605MJCNZR01",
                "confidence": 0.93,
            },
            "cards.jugador_10.nombre": {"text": "", "confidence": 0.0},
            "cards.jugador_10.apellidos": {"text": "", "confidence": 0.0},
            "cards.jugador_10.nacimiento": {"text": "", "confidence": 0.0},
            "cards.jugador_10.curp": {"text": "", "confidence": 0.0},
        },
    )
    monkeypatch.setattr(
        extractor,
        "_read_template_field",
        lambda field_info: local_registration_ocr.OCRTextResult(
            field_info.get("text", "") if isinstance(field_info, dict) else "",
            field_info.get("confidence", 0.0) if isinstance(field_info, dict) else 0.0,
        ),
    )

    raw = {}
    extraction = extractor._extract_with_template_alignment(
        Image.new("RGB", (1000, 800), "white"),
        raw,
    )

    assert extraction["is_front"] is False
    assert extraction["players"][0]["visible_player_number"] == 9
    assert extraction["players"][0]["continuous_player_number"] == 9
    assert extraction["players"][0]["source_page_number"] == 2
    assert raw["ctt_template"]["page_type"] == "back"
    assert raw["ctt_template"]["back_page_repeatable"] is True
    assert len(extraction["players"]) == 1
    RegistrationFormExtraction.model_validate(extraction)


def test_pdf_page_aggregation_groups_repeated_back_pages():
    extractor = object.__new__(local_registration_ocr.LocalRegistrationOCR)
    page_payloads = iter(
        [
            {
                "extraction": {
                    "team": {"name": "Equipo Uno"},
                    "manager": None,
                    "responsables": [{"name": "Director Uno"}],
                    "players": [
                        {
                            "name": "Jugador Uno",
                            "visible_player_number": 1,
                            "continuous_player_number": 1,
                        }
                    ],
                    "is_front": True,
                    "form_type": "copa_telmex_telcel_futbol_2026",
                },
                "raw": {
                    "ctt_template": {
                        "side": "front",
                        "template_id": "copa_telmex_telcel_futbol_2026",
                        "tournament": "Copa Telmex-Telcel",
                        "sport": "futbol",
                    }
                },
            },
            {
                "extraction": {
                    "team": {"name": "Unknown Team"},
                    "players": [
                        {
                            "name": "Jugador Nueve",
                            "visible_player_number": 9,
                            "continuous_player_number": 9,
                        }
                    ],
                    "is_front": False,
                },
                "raw": {
                    "ctt_template": {
                        "side": "back",
                        "template_id": "copa_telmex_telcel_futbol_2026",
                        "back_page_repeatable": True,
                    }
                },
            },
            {
                "extraction": {
                    "team": {"name": "Unknown Team"},
                    "players": [
                        {
                            "name": "Jugador Veintiuno",
                            "visible_player_number": 9,
                            "continuous_player_number": 9,
                        }
                    ],
                    "is_front": False,
                },
                "raw": {
                    "ctt_template": {
                        "side": "back",
                        "template_id": "copa_telmex_telcel_futbol_2026",
                        "back_page_repeatable": True,
                    }
                },
            },
            {
                "extraction": {
                    "team": {"name": "Equipo Dos"},
                    "players": [],
                    "is_front": True,
                    "form_type": "copa_telmex_telcel_futbol_2026",
                },
                "raw": {
                    "ctt_template": {
                        "side": "front",
                        "template_id": "copa_telmex_telcel_futbol_2026",
                    }
                },
            },
        ]
    )

    def fake_extract(_image):
        return next(page_payloads)

    extractor.extract = fake_extract
    payload = extractor.extract_pdf_pages(
        [Image.new("RGB", (10, 10), "white") for _ in range(4)],
        source_path="/tmp/lote.pdf",
    )

    assert payload["document"]["page_count"] == 4
    assert payload["document"]["team_count"] == 2
    assert payload["teams"][0]["team"]["name"] == "Equipo Uno"
    assert payload["teams"][0]["back_page_count"] == 2
    assert [p["continuous_player_number"] for p in payload["teams"][0]["players"]] == [
        1,
        9,
        21,
    ]
    assert payload["teams"][0]["players"][2]["visible_player_number"] == 9
    assert payload["teams"][0]["players"][2]["document_page_number"] == 3
    assert payload["teams"][1]["team"]["name"] == "Equipo Dos"


def test_openai_page_payload_normalizes_ctt_front(monkeypatch):
    def fake_openai_json(*, image, prompt, model=None, timeout_seconds=90.0):
        assert "Copa Telmex-Telcel de futbol" in prompt
        return {
            "page_type": "front",
            "team": {
                "name": "Tlaxcoyan JR",
                "gender": "Varonil",
                "category": "Juvenil",
                "representative_name": "Everardo Soto Diaz",
                "league": "Futbol Federal de Veracruz",
                "email": None,
                "state": "Veracruz",
                "municipality": "Tlaxcoyan",
                "folio": "VER-0001-J",
                "confidence": 0.9,
            },
            "responsables": [
                {
                    "role": "director_tecnico",
                    "name": "Everardo Soto Diaz",
                    "birth_date": "19/12/1980",
                    "curp": None,
                    "confidence": 0.8,
                    "needs_review": False,
                },
                {
                    "role": "auxiliar",
                    "name": None,
                    "birth_date": None,
                    "curp": None,
                    "confidence": 0,
                    "needs_review": False,
                },
            ],
            "players": [
                {
                    "visible_player_number": 1,
                    "name": "Josimar Abundio Cortes",
                    "birth_date": "14/11/2010",
                    "curp": None,
                    "confidence": 0.82,
                    "needs_review": False,
                }
            ],
            "overall_confidence": 0.86,
            "notes": "test",
        }

    monkeypatch.setattr(local_registration_ocr, "_openai_vision_json", fake_openai_json)

    payload = local_registration_ocr._openai_page_payload(
        Image.new("RGB", (100, 100), "white"),
        page_number=1,
    )
    extraction = payload["extraction"]

    assert extraction["is_front"] is True
    assert extraction["team"]["name"] == "Tlaxcoyan JR"
    assert extraction["team"]["gender"] == "Varonil"
    assert extraction["manager"]["name"] == "Everardo Soto Diaz"
    assert len(extraction["responsables"]) == 1
    assert extraction["responsables"][0]["role"] == "director_tecnico"
    assert extraction["players"][0]["visible_player_number"] == 1
    assert payload["raw"]["provider"] == "openai"
    assert payload["raw"]["ctt_template"]["sport"] == "futbol"
    RegistrationFormExtraction.model_validate(extraction)


def test_trocr_retries_download_when_cache_is_missing(monkeypatch):
    monkeypatch.setenv("LOCAL_OCR_USE_TEMPLATE_ALIGNER", "0")
    monkeypatch.delenv("LOCAL_OCR_ALLOW_DOWNLOAD", raising=False)

    attempts = []

    class _FakeProcessor:
        @classmethod
        def from_pretrained(cls, model_name, local_files_only):
            attempts.append(("processor", model_name, local_files_only))
            if local_files_only:
                raise OSError("cache miss")
            return cls()

    class _FakeModel:
        @classmethod
        def from_pretrained(cls, model_name, local_files_only):
            attempts.append(("model", model_name, local_files_only))
            if local_files_only:
                raise OSError("cache miss")
            return cls()

        def to(self, device):
            return self

        def eval(self):
            return self

    monkeypatch.setattr(local_registration_ocr, "TrOCRProcessor", _FakeProcessor)
    monkeypatch.setattr(local_registration_ocr, "VisionEncoderDecoderModel", _FakeModel)

    helper = local_registration_ocr.TrOCRHelper()

    assert helper.initialize() is True
    assert helper.status.available is True
    assert helper.status.source == "download"
    assert attempts == [
        ("processor", "microsoft/trocr-base-handwritten", True),
        ("processor", "microsoft/trocr-base-handwritten", False),
        ("model", "microsoft/trocr-base-handwritten", False),
    ]


def test_mineru_text_seeds_registration_extraction(monkeypatch):
    monkeypatch.setenv("MINERU_ENABLED", "1")
    monkeypatch.setenv("LOCAL_OCR_USE_TEMPLATE_ALIGNER", "0")
    monkeypatch.setenv("LOCAL_OCR_LAYOUT_PROVIDER", "")
    monkeypatch.setenv("LOCAL_OCR_USE_MOONDREAM", "0")
    monkeypatch.setenv("LOCAL_OCR_USE_TROCR", "0")
    monkeypatch.setattr(
        local_registration_ocr,
        "parse_document_bytes",
        lambda _data, suffix=".jpg": MinerUParseResult(
            enabled=True,
            text="\n".join(
                [
                    "Equipo: Club Deportivo Norte",
                    "Categoria: Sub-15",
                    "Estado: Jalisco",
                    "Delegado: Luis Garcia",
                    "Juan Perez Lopez 01/02/2011 PELJ110201HJCRPN09",
                ]
            ),
        ),
    )

    extractor = local_registration_ocr.LocalRegistrationOCR()
    payload = extractor.extract(Image.new("RGB", (1000, 800), "white"))
    extraction = payload["extraction"]

    assert extraction["team"]["name"] == "Club Deportivo Norte"
    assert extraction["team"]["category"] == "Sub-15"
    assert extraction["manager"]["name"] == "Luis Garcia"
    assert extraction["players"][0]["name"] == "Juan Perez Lopez"
    assert extraction["players"][0]["birth_date"] == "01/02/2011"
    assert extraction["players"][0]["curp"] == "PELJ110201HJCRPN09"
    assert extraction["players"][0]["needs_review"] is True
    assert payload["raw"]["mineru"]["text_length"] > 0
    assert "parse=mineru" in extraction["notes"]


def test_registration_ocr_records_adjudication_metadata(monkeypatch):
    monkeypatch.setenv("MINERU_ENABLED", "1")
    monkeypatch.setenv("REGISTRATION_ADJUDICATOR_ENABLED", "1")
    monkeypatch.setenv("LOCAL_OCR_USE_TEMPLATE_ALIGNER", "0")
    monkeypatch.setenv("LOCAL_OCR_LAYOUT_PROVIDER", "")
    monkeypatch.setenv("LOCAL_OCR_USE_MOONDREAM", "0")
    monkeypatch.setenv("LOCAL_OCR_USE_TROCR", "0")
    monkeypatch.setattr(
        local_registration_ocr,
        "parse_document_bytes",
        lambda _data, suffix=".jpg": MinerUParseResult(
            enabled=True,
            text="\n".join(
                [
                    "Equipo: Club Deportivo Norte",
                    "Juan Perez Lopez 01/02/2011 PELJ110201HJCRPN09",
                ]
            ),
        ),
    )

    def fake_adjudicate(current, mineru, *, mineru_text=""):
        result = dict(current)
        result["notes"] = "adjudicated"
        return RegistrationAdjudicationResult(
            extraction=result,
            applied=True,
            raw={"provider": "ollama", "model": "qwen3:4b"},
        )

    monkeypatch.setattr(
        local_registration_ocr,
        "adjudicate_registration_extraction",
        fake_adjudicate,
    )

    extractor = local_registration_ocr.LocalRegistrationOCR()
    payload = extractor.extract(Image.new("RGB", (1000, 800), "white"))

    assert payload["raw"]["adjudication"]["applied"] is True
    assert payload["raw"]["adjudication"]["provider"] == "ollama"
