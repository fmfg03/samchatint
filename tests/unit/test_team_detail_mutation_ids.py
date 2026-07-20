from pathlib import Path


def test_mutation_forms_use_uuid_v4_fallback_outside_secure_contexts():
    template = (
        Path(__file__).resolve().parents[2] / "templates" / "team_detail.html"
    ).read_text(encoding="utf-8")

    assert template.count("crypto.randomUUID()") == 1
    assert "crypto.getRandomValues(bytes)" in template
    assert "bytes[6] = (bytes[6] & 0x0f) | 0x40" in template
    assert "bytes[8] = (bytes[8] & 0x3f) | 0x80" in template
    assert template.count("newMutationRequestId()") == 4
