import json as jsonlib
import base64
import http.cookiejar
import mimetypes
import os
import urllib.error
import urllib.request
import urllib.parse
from typing import Dict, Optional, Any, Union, Sequence, Tuple, List
from uuid import uuid4
from .models import Response

TimeoutType = Optional[Union[int, float, Tuple[Optional[float], Optional[float]]]]


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Session:
    """
    HTTP Session to manage headers, cookies, etc.
    """
    def __init__(self, headers: Optional[Dict[str, str]] = None):
        self.headers: Dict[str, str] = {
            'User-Agent': 'easyget/1.0.1'
        }
        if headers:
            self.headers.update(headers)
        self.cookies = http.cookiejar.CookieJar()
        cookie_processor = urllib.request.HTTPCookieProcessor(self.cookies)
        self._opener = urllib.request.build_opener(cookie_processor)
        self._opener_no_redirect = urllib.request.build_opener(cookie_processor, _NoRedirectHandler())
        self._open_responses = set()

    def _build_url(self, url: str, params: Optional[Union[Dict[str, Any], Sequence[Tuple[str, Any]]]] = None) -> str:
        if not params:
            return url

        split = urllib.parse.urlsplit(url)
        existing_pairs = urllib.parse.parse_qsl(split.query, keep_blank_values=True)
        new_query = urllib.parse.urlencode(params, doseq=True)
        new_pairs = urllib.parse.parse_qsl(new_query, keep_blank_values=True)
        merged_query = urllib.parse.urlencode(existing_pairs + new_pairs, doseq=True)
        return urllib.parse.urlunsplit((split.scheme, split.netloc, split.path, merged_query, split.fragment))

    @staticmethod
    def _encode_basic_auth(auth: Tuple[str, str]) -> str:
        username, password = auth
        token = f"{username}:{password}".encode("utf-8")
        return "Basic " + base64.b64encode(token).decode("ascii")

    @staticmethod
    def _format_cookie_header(cookies: Dict[str, Any]) -> str:
        pairs = [f"{key}={value}" for key, value in cookies.items()]
        return "; ".join(pairs)

    @staticmethod
    def _read_file_payload(file_data: Any) -> bytes:
        if hasattr(file_data, "read"):
            payload = file_data.read()
        else:
            payload = file_data

        if isinstance(payload, str):
            return payload.encode("utf-8")
        if isinstance(payload, (bytes, bytearray)):
            return bytes(payload)

        raise TypeError(f"Unsupported file payload type: {type(payload).__name__}")

    @staticmethod
    def _normalize_form_items(data: Optional[Any]) -> List[Tuple[str, str]]:
        if data is None:
            return []
        if isinstance(data, dict):
            iterator = data.items()
        elif isinstance(data, (list, tuple)):
            iterator = data
        else:
            raise TypeError("multipart form fields must be dict or sequence of tuples")

        items: List[Tuple[str, str]] = []
        for key, value in iterator:
            if isinstance(value, (list, tuple)):
                for sub_value in value:
                    items.append((str(key), str(sub_value)))
            else:
                items.append((str(key), str(value)))
        return items

    @classmethod
    def _encode_multipart(
        cls,
        data: Optional[Any],
        files: Dict[str, Any],
        req_headers: Dict[str, str],
    ) -> bytes:
        boundary = f"easyget-{uuid4().hex}"
        lines: List[bytes] = []

        for key, value in cls._normalize_form_items(data):
            lines.append(f"--{boundary}\r\n".encode("utf-8"))
            lines.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
            lines.append(value.encode("utf-8"))
            lines.append(b"\r\n")

        for field_name, raw_value in files.items():
            filename = field_name
            content_type = "application/octet-stream"
            file_value = raw_value

            if isinstance(raw_value, tuple):
                if len(raw_value) == 2:
                    filename, file_value = raw_value
                elif len(raw_value) == 3:
                    filename, file_value, content_type = raw_value
                else:
                    raise TypeError("file tuple must be (filename, data) or (filename, data, content_type)")
            elif hasattr(raw_value, "name"):
                filename = os.path.basename(raw_value.name) or field_name

            if content_type == "application/octet-stream":
                guessed = mimetypes.guess_type(str(filename))[0]
                if guessed:
                    content_type = guessed

            payload = cls._read_file_payload(file_value)
            safe_name = str(filename).replace('"', "")

            lines.append(f"--{boundary}\r\n".encode("utf-8"))
            lines.append(
                f'Content-Disposition: form-data; name="{field_name}"; filename="{safe_name}"\r\n'.encode("utf-8")
            )
            lines.append(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
            lines.append(payload)
            lines.append(b"\r\n")

        lines.append(f"--{boundary}--\r\n".encode("utf-8"))
        req_headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        return b"".join(lines)

    @staticmethod
    def _normalize_timeout(timeout: TimeoutType) -> Optional[float]:
        if isinstance(timeout, tuple):
            if len(timeout) != 2:
                raise ValueError("timeout tuple must be (connect_timeout, read_timeout)")
            connect_timeout, read_timeout = timeout
            if read_timeout is not None:
                return float(read_timeout)
            if connect_timeout is not None:
                return float(connect_timeout)
            return None
        if timeout is None:
            return None
        return float(timeout)

    @staticmethod
    def _normalize_data(
        data: Optional[Any],
        json: Optional[Any],
        files: Optional[Dict[str, Any]],
        req_headers: Dict[str, str],
    ) -> Optional[Union[bytes, bytearray]]:
        if data is not None and json is not None:
            raise TypeError("cannot use both 'data' and 'json' in the same request")
        if json is not None and files is not None:
            raise TypeError("cannot use both 'json' and 'files' in the same request")
        if files is not None:
            return Session._encode_multipart(data, files, req_headers)
        if json is not None:
            req_headers.setdefault("Content-Type", "application/json")
            return jsonlib.dumps(json).encode("utf-8")
        if data is None:
            return None
        if isinstance(data, (bytes, bytearray)):
            return data
        if isinstance(data, str):
            return data.encode("utf-8")
        if isinstance(data, (dict, list, tuple)):
            req_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
            return urllib.parse.urlencode(data, doseq=True).encode("utf-8")

        raise TypeError(f"Unsupported request body type: {type(data).__name__}")

    def request(self, method: str, url: str, 
                params: Optional[Union[Dict[str, Any], Sequence[Tuple[str, Any]]]] = None,
                data: Optional[Any] = None,
                json: Optional[Any] = None,
                files: Optional[Dict[str, Any]] = None,
                auth: Optional[Tuple[str, str]] = None,
                cookies: Optional[Dict[str, Any]] = None,
                headers: Optional[Dict[str, str]] = None,
                timeout: TimeoutType = 30,
                stream: bool = False,
                allow_redirects: bool = True) -> Response:
        method = method.upper()
        url = self._build_url(url, params=params)
            
        req_headers = self.headers.copy()
        if headers:
            req_headers.update(headers)
        if auth is not None:
            req_headers["Authorization"] = self._encode_basic_auth(auth)
        if cookies:
            req_headers["Cookie"] = self._format_cookie_header(cookies)
        req_data = self._normalize_data(data, json, files, req_headers)
        normalized_timeout = self._normalize_timeout(timeout)
        req = urllib.request.Request(url, data=req_data, headers=req_headers, method=method)
        opener = self._opener if allow_redirects else self._opener_no_redirect
        
        try:
            # We must be careful with 'with' if we want to stream
            resp = opener.open(req, timeout=normalized_timeout)
            response = Response(status_code=resp.status, headers=dict(resp.headers), url=url)
            
            if stream:
                response._stream_response = resp
                self._open_responses.add(response)
                response.add_close_callback(lambda: self._open_responses.discard(response))
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
                response.add_close_callback(lambda: self._open_responses.discard(response))
            else:
                response._content = e.read()
            return response
        except urllib.error.URLError as e:
            from .exceptions import DownloadError
            raise DownloadError(f"Request failed: {e}")

    def get(self, url: str, **kwargs) -> Response:
        return self.request('GET', url, **kwargs)

    def post(self, url: str, data: Any = None, **kwargs) -> Response:
        return self.request('POST', url, data=data, **kwargs)

    def head(self, url: str, **kwargs) -> Response:
        return self.request('HEAD', url, **kwargs)

    def put(self, url: str, data: Any = None, **kwargs) -> Response:
        return self.request('PUT', url, data=data, **kwargs)

    def patch(self, url: str, data: Any = None, **kwargs) -> Response:
        return self.request('PATCH', url, data=data, **kwargs)

    def delete(self, url: str, **kwargs) -> Response:
        return self.request('DELETE', url, **kwargs)

    def options(self, url: str, **kwargs) -> Response:
        return self.request('OPTIONS', url, **kwargs)

    def close(self):
        for response in list(self._open_responses):
            response.close()
            self._open_responses.discard(response)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

def request(method: str, url: str, **kwargs) -> Response:
    stream = bool(kwargs.get("stream"))
    if not stream:
        with Session() as s:
            return s.request(method, url, **kwargs)

    session = Session()
    try:
        response = session.request(method, url, **kwargs)
    except Exception:
        session.close()
        raise

    response.add_close_callback(session.close)
    return response

def get(url: str, **kwargs) -> Response:
    return request("GET", url, **kwargs)

def post(url: str, **kwargs) -> Response:
    return request("POST", url, **kwargs)

def put(url: str, **kwargs) -> Response:
    return request("PUT", url, **kwargs)

def patch(url: str, **kwargs) -> Response:
    return request("PATCH", url, **kwargs)

def delete(url: str, **kwargs) -> Response:
    return request("DELETE", url, **kwargs)

def head(url: str, **kwargs) -> Response:
    return request("HEAD", url, **kwargs)

def options(url: str, **kwargs) -> Response:
    return request("OPTIONS", url, **kwargs)
