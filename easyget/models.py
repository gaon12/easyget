import json
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
                self._content = self._stream_response.read()
            else:
                self._content = b""
        return self._content

    @property
    def text(self) -> str:
        if self._text is None:
            encoding = 'utf-8' # Simplified: should detect from headers
            self._text = self.content.decode(encoding, errors='ignore')
        return self._text

    def json(self):
        return json.loads(self.text)

    def iter_bytes(self, chunk_size: int = 1024) -> Iterator[bytes]:
        if self._stream_response:
            while True:
                chunk = self._stream_response.read(chunk_size)
                if not chunk: break
                yield chunk

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            from .exceptions import DownloadError
            raise DownloadError(f"HTTP Error: {self.status_code} for url: {self.url}")
