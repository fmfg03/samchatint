from devnous.gastos.services import cfdi_parser


def test_download_xml_blocks_localhost_without_network(monkeypatch):
    calls = []

    def _fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("network must not be called for localhost URLs")

    monkeypatch.setattr(cfdi_parser.requests, "get", _fake_get)

    assert cfdi_parser.download_xml("http://127.0.0.1:8000/private.xml") is None
    assert cfdi_parser.download_xml("file:///tmp/private.xml") is None
    assert calls == []


def test_download_xml_rejects_oversized_response(monkeypatch):
    class _Response:
        status_code = 200

        def iter_content(self, chunk_size=1):
            yield b"x" * (cfdi_parser.MAX_CFDI_XML_DOWNLOAD_BYTES + 1)

    monkeypatch.setattr(
        cfdi_parser.requests,
        "get",
        lambda *_args, **_kwargs: _Response(),
    )

    assert cfdi_parser.download_xml("https://example.com/cfdi.xml") is None


def test_download_xml_returns_small_xml(monkeypatch):
    class _Response:
        status_code = 200

        def iter_content(self, chunk_size=1):
            yield b"<?xml version='1.0'?><Comprobante />"

    observed = {}

    def _fake_get(url, **kwargs):
        observed["url"] = url
        observed["kwargs"] = kwargs
        return _Response()

    monkeypatch.setattr(cfdi_parser.requests, "get", _fake_get)

    assert cfdi_parser.download_xml("https://example.com/cfdi.xml") == (
        "<?xml version='1.0'?><Comprobante />"
    )
    assert observed["kwargs"]["stream"] is True
    assert observed["kwargs"]["allow_redirects"] is False
