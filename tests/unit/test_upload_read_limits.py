import pytest

from devnous.gastos.utils.receipt_bytes import read_upload_limited


class _ChunkedUpload:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.bytes_returned = 0
        self.read_calls = 0

    async def read(self, _size=-1):
        self.read_calls += 1
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        self.bytes_returned += len(chunk)
        return chunk


@pytest.mark.asyncio
async def test_read_upload_limited_rejects_before_consuming_full_upload():
    upload = _ChunkedUpload([b"abc", b"def", b"ghi"])

    with pytest.raises(ValueError, match="too large"):
        await read_upload_limited(
            upload,
            max_bytes=5,
            too_large_message="too large",
            chunk_size=3,
        )

    assert upload.bytes_returned == 6
    assert upload.read_calls == 2


@pytest.mark.asyncio
async def test_read_upload_limited_returns_small_upload():
    upload = _ChunkedUpload([b"abc", b"de", b""])

    payload = await read_upload_limited(
        upload,
        max_bytes=5,
        too_large_message="too large",
        chunk_size=3,
    )

    assert payload == b"abcde"
