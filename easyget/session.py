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

    def request(self, method: str, url: str, 
                params: Optional[Dict[str, Any]] = None,
                data: Optional[Any] = None,
                headers: Optional[Dict[str, str]] = None,
                timeout: int = 30,
                stream: bool = False) -> Response:
        
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
            
        req_headers = self.headers.copy()
        if headers:
            req_headers.update(headers)
            
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        
        try:
            # We must be careful with 'with' if we want to stream
            resp = urllib.request.urlopen(req, timeout=timeout)
            response = Response(status_code=resp.status, headers=dict(resp.headers), url=url)
            
            if stream:
                response._stream_response = resp
            else:
                with resp:
                    response._content = resp.read()
            return response
        except urllib.error.HTTPError as e:
            # Even on error, we might want the response object
            response = Response(status_code=e.code, headers=dict(e.headers), url=url)
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

def get(url: str, **kwargs) -> Response:
    with Session() as s: # Using as context manager if implemented
        return s.get(url, **kwargs)

# Add simple context manager support to Session
Session.__enter__ = lambda self: self
Session.__exit__ = lambda self, *args: None
