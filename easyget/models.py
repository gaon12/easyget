import json
import re
from typing import Dict, Optional, Iterator

class Response:
    """
    HTTP Response object similar to requests.Response.
    """
    def __init__(self, status_code: int, headers: Dict[str, str], url: str):
        self.status_code = int(status_code)
        self.headers = headers
        self.url = url
        self._content: Optional[bytes] = None
        self._text: Optional[str] = None
        self._stream_response = None # Placeholder for the raw response object

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

    def iter_bytes(self, chunk_size: int = 1024) -> Iterator[bytes]:
        if self._content is not None:
            for idx in range(0, len(self._content), chunk_size):
                yield self._content[idx:idx + chunk_size]
            return

        if self._stream_response:
            chunks = []
            try:
                while True:
                    chunk = self._stream_response.read(chunk_size)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    yield chunk
            finally:
                self._content = b"".join(chunks)
                self.close()

    def close(self):
        if self._stream_response:
            try:
                self._stream_response.close()
            finally:
                self._stream_response = None

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            from .exceptions import DownloadError
            raise DownloadError(f"HTTP Error: {self.status_code} for url: {self.url}")
