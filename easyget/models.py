import gzip
import json
import re
import zlib
from collections.abc import Callable, Iterator
from contextlib import suppress


class _DeflateReader:
    """
    Incremental reader for Content-Encoding: deflate bodies.
    Servers send either zlib-wrapped or raw deflate streams; the first
    decompression attempt retries with raw DEFLATE if zlib framing fails.
    """

    def __init__(self, raw):
        self._raw = raw
        self._decompressor = zlib.decompressobj()
        self._retried_raw = False
        self._buffer = bytearray()
        self._eof = False

    def _fill(self):
        if self._eof:
            return
        chunk = self._raw.read(65536)
        if not chunk:
            self._eof = True
            self._buffer.extend(self._decompressor.flush())
            return
        try:
            self._buffer.extend(self._decompressor.decompress(chunk))
        except zlib.error:
            if self._retried_raw:
                raise
            self._retried_raw = True
            self._decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
            self._buffer.extend(self._decompressor.decompress(chunk))

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            while not self._eof:
                self._fill()
            data = bytes(self._buffer)
            self._buffer.clear()
            return data
        while len(self._buffer) < size and not self._eof:
            self._fill()
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        return data


class Response:
    """
    HTTP Response object similar to requests.Response.
    """

    def __init__(self, status_code: int, headers: dict[str, str], url: str):
        self.status_code = int(status_code)
        self.headers = headers
        self.url = url
        self._content: bytes | None = None
        self._text: str | None = None
        self._stream_response = None  # Placeholder for the raw response object
        self._closed = False
        self._close_callbacks: list[Callable[[], None]] = []
        self._auto_decompress = False
        self._content_decoded = False

    def _decode_content(self, content: bytes) -> bytes:
        encoding = self.headers.get("Content-Encoding", "").strip().lower()
        if not self._auto_decompress or not encoding:
            return content

        try:
            if encoding == "gzip":
                return gzip.decompress(content)
            if encoding == "deflate":
                try:
                    return zlib.decompress(content)
                except zlib.error:
                    return zlib.decompress(content, -zlib.MAX_WBITS)
        except Exception:
            # Invalid or truncated payload should not crash response consumption.
            return content
        return content

    @property
    def content(self) -> bytes:
        if self._content is None:
            if self._stream_response:
                try:
                    self._content = self._stream_response.read()
                finally:
                    self.close()
            else:
                self._content = b""
        if not self._content_decoded:
            self._content = self._decode_content(self._content)
            self._content_decoded = True
        return self._content

    @property
    def text(self) -> str:
        if self._text is None:
            encoding = "utf-8"
            content_type = self.headers.get("Content-Type", "")
            match = re.search(r"charset=([^\s;]+)", content_type, re.IGNORECASE)
            if match:
                encoding = match.group(1).strip("'\"")

            raw = self.content
            try:
                self._text = raw.decode(encoding, errors="replace")
            except LookupError:
                self._text = raw.decode("utf-8", errors="replace")
        return self._text

    def json(self):
        return json.loads(self.text)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    @property
    def status(self) -> int:
        # aiohttp compatibility alias
        return self.status_code

    def _decoded_stream(self):
        """
        Wrap the raw stream in a decompressor when --compressed is active.
        Keeps download memory bounded for large compressed bodies.
        """
        encoding = self.headers.get("Content-Encoding", "").strip().lower()
        if not self._auto_decompress or not self._stream_response:
            return self._stream_response
        if encoding == "gzip":
            return gzip.GzipFile(fileobj=self._stream_response)
        if encoding == "deflate":
            return _DeflateReader(self._stream_response)
        return self._stream_response

    def iter_bytes(self, chunk_size: int = 1024) -> Iterator[bytes]:
        if self._content is not None:
            decoded = self.content
            for idx in range(0, len(decoded), chunk_size):
                yield decoded[idx : idx + chunk_size]
            return

        if self._stream_response:
            # Streamed bodies are consumed once and never retained in memory,
            # matching requests' semantics for iter_content(). / 스트림 바디는
            # 한 번만 소비하며 메모리에 보관하지 않습니다.
            source = self._decoded_stream()
            try:
                while True:
                    chunk = source.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
            finally:
                self._content = b""
                self._content_decoded = True
                self.close()

    def close(self):
        if self._closed:
            return

        if self._stream_response:
            try:
                self._stream_response.close()
            finally:
                self._stream_response = None
        self._closed = True

        callbacks = self._close_callbacks[:]
        self._close_callbacks.clear()
        for callback in callbacks:
            # Close path must be best-effort and never mask caller errors.
            with suppress(Exception):
                callback()

    def add_close_callback(self, callback: Callable[[], None]):
        self._close_callbacks.append(callback)

    def set_auto_decompress(self, enabled: bool):
        self._auto_decompress = bool(enabled)
        if self._content is not None:
            self._content_decoded = False

    def summary(
        self,
        *,
        include_body: bool = False,
        max_body_chars: int = 512,
        compact: bool = False,
    ) -> dict[str, object]:
        """
        Serialize response metadata for logs, automation, or LLM-friendly traces.
        """
        if compact:
            payload: dict[str, object] = {
                "st": self.status_code,
                "ok": 1 if self.ok else 0,
                "u": self.url,
            }
            if include_body:
                payload["b"] = self.text[: max(0, int(max_body_chars))]
            return payload

        payload = {
            "status_code": self.status_code,
            "ok": self.ok,
            "url": self.url,
            "headers": self.headers,
        }
        if include_body:
            payload["body_preview"] = self.text[: max(0, int(max_body_chars))]
        return payload

    @property
    def closed(self) -> bool:
        return self._closed

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            from .exceptions import HTTPStatusError

            raise HTTPStatusError(
                f"HTTP Error: {self.status_code} for url: {self.url}",
                context={"status_code": self.status_code, "url": self.url},
            )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False
