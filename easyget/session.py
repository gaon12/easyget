import urllib.request
import urllib.parse
from typing import Dict, Optional, Any, Union
from .models import Response

class Session:
    """
    HTTP Session to manage headers, cookies, etc.
    """
    def __init__(self):
        self.headers: Dict[str, str] = {
            'User-Agent': 'easyget/1.0.1'
        }
        self.cookies = {} # Placeholder for cookie management
        self._open_responses = set()

    def _build_url(self, url: str, params: Optional[Dict[str, Any]] = None) -> str:
        if not params:
            return url

        split = urllib.parse.urlsplit(url)
        existing_pairs = urllib.parse.parse_qsl(split.query, keep_blank_values=True)
        new_query = urllib.parse.urlencode(params, doseq=True)
        new_pairs = urllib.parse.parse_qsl(new_query, keep_blank_values=True)
        merged_query = urllib.parse.urlencode(existing_pairs + new_pairs, doseq=True)
        return urllib.parse.urlunsplit((split.scheme, split.netloc, split.path, merged_query, split.fragment))

    @staticmethod
    def _normalize_data(data: Optional[Any], req_headers: Dict[str, str]) -> Optional[Union[bytes, bytearray]]:
        if data is None:
            return None
        if isinstance(data, (bytes, bytearray)):
            return data
        if isinstance(data, str):
            return data.encode("utf-8")
        if isinstance(data, dict):
            req_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
            return urllib.parse.urlencode(data, doseq=True).encode("utf-8")

        raise TypeError(f"Unsupported request body type: {type(data).__name__}")

    def request(self, method: str, url: str, 
                params: Optional[Dict[str, Any]] = None,
                data: Optional[Any] = None,
                headers: Optional[Dict[str, str]] = None,
                timeout: int = 30,
                stream: bool = False) -> Response:
        url = self._build_url(url, params=params)
            
        req_headers = self.headers.copy()
        if headers:
            req_headers.update(headers)
        req_data = self._normalize_data(data, req_headers)
        req = urllib.request.Request(url, data=req_data, headers=req_headers, method=method)
        
        try:
            # We must be careful with 'with' if we want to stream
            resp = urllib.request.urlopen(req, timeout=timeout)
            response = Response(status_code=resp.status, headers=dict(resp.headers), url=url)
            
            if stream:
                response._stream_response = resp
                self._open_responses.add(response)
            else:
                with resp:
                    response._content = resp.read()
            return response
        except urllib.error.HTTPError as e:
            # Even on error, we might want the response object
            response = Response(status_code=e.code, headers=dict(e.headers), url=url)
            if stream:
                response._stream_response = e
                self._open_responses.add(response)
            else:
                response._content = e.read()
            return response
        except Exception as e:
            from .exceptions import DownloadError
            raise DownloadError(f"Request failed: {e}")

    def get(self, url: str, **kwargs) -> Response:
        return self.request('GET', url, **kwargs)

    def post(self, url: str, data: Any = None, **kwargs) -> Response:
        return self.request('POST', url, data=data, **kwargs)

    def head(self, url: str, **kwargs) -> Response:
        return self.request('HEAD', url, **kwargs)

    def close(self):
        for response in list(self._open_responses):
            response.close()
            self._open_responses.discard(response)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

def get(url: str, **kwargs) -> Response:
    with Session() as s:
        return s.get(url, **kwargs)
