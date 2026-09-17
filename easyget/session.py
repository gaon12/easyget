import base64
import http.cookiejar
import json as jsonlib
import mimetypes
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any
from uuid import uuid4

from . import __version__
from .exceptions import RequestError
from .models import Response

TimeoutType = int | float | tuple[float | None, float | None] | None
VerifyType = bool | str | os.PathLike[str]
CertType = (
    str | os.PathLike[str] | tuple[str | os.PathLike[str], str | os.PathLike[str]]
)
ProxyType = str | dict[str, str]
ResponseHookType = (
    Callable[[Response, dict[str, Any]], Response | None]
    | Sequence[Callable[[Response, dict[str, Any]], Response | None]]
)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _same_origin(url_a: str, url_b: str) -> bool:
    """Compare scheme, host, and effective port of two URLs."""
    a = urllib.parse.urlsplit(url_a)
    b = urllib.parse.urlsplit(url_b)
    return (
        a.scheme.lower() == b.scheme.lower()
        and (a.hostname or "").lower() == (b.hostname or "").lower()
        and (a.port or _DEFAULT_PORTS.get(a.scheme.lower()))
        == (b.port or _DEFAULT_PORTS.get(b.scheme.lower()))
    )


def _check_url_scheme(url: str) -> None:
    """easyget only speaks HTTP(S); reject file://, ftp://, etc. outright."""
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise RequestError(
            f"Unsupported URL scheme: {scheme or '(missing)'}",
            hint="Only http:// and https:// URLs are supported.",
            context={"url": url},
            retryable=False,
        )


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """
    Hardened redirect handling:
    - refuses redirects to non-HTTP(S) schemes (e.g. file:// local file reads)
    - drops Authorization/Cookie headers when the redirect crosses origins,
      matching requests' credential-stripping behavior
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        new_scheme = urllib.parse.urlsplit(new_req.full_url).scheme.lower()
        if new_scheme not in _ALLOWED_SCHEMES:
            raise RequestError(
                f"Refused redirect to unsupported scheme: {new_scheme or 'unknown'}",
                hint="easyget only follows http/https redirects.",
                context={"url": new_req.full_url},
                retryable=False,
            )
        if not _same_origin(req.full_url, new_req.full_url):
            new_req.remove_header("Authorization")
            new_req.remove_header("Cookie")
        return new_req


class Session:
    """
    HTTP Session to manage headers, cookies, etc.
    """

    def __init__(
        self,
        headers: dict[str, str] | None = None,
        verify: VerifyType = True,
        cert: CertType | None = None,
        proxies: ProxyType | None = None,
    ):
        self.headers: dict[str, str] = {"User-Agent": f"easyget/{__version__}"}
        if headers:
            self.headers.update(headers)
        self._default_verify: VerifyType = verify
        self._default_cert: CertType | None = cert
        self._default_proxies: ProxyType | None = proxies
        self.cookies = http.cookiejar.CookieJar()
        self._opener = self._build_transport_opener(
            allow_redirects=True,
            verify=self._default_verify,
            cert=self._default_cert,
            proxies=self._default_proxies,
        )
        self._opener_no_redirect = self._build_transport_opener(
            allow_redirects=False,
            verify=self._default_verify,
            cert=self._default_cert,
            proxies=self._default_proxies,
        )
        self._open_responses = set()

    @staticmethod
    def _normalize_proxy_map(proxies: ProxyType | None) -> dict[str, str] | None:
        if proxies is None:
            return None
        if isinstance(proxies, str):
            return {"http": proxies, "https": proxies}
        if isinstance(proxies, dict):
            return {str(k): str(v) for k, v in proxies.items()}
        raise TypeError("proxies must be a mapping or string URL")

    @staticmethod
    def _build_ssl_context(
        verify: VerifyType,
        cert: CertType | None,
    ) -> ssl.SSLContext | None:
        needs_context = (
            cert is not None
            or verify is False
            or isinstance(verify, (str, os.PathLike))
        )
        if not needs_context:
            return None

        context = ssl.create_default_context()

        if verify is False:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        elif isinstance(verify, (str, os.PathLike)):
            context.load_verify_locations(cafile=str(verify))
        elif verify is not True:
            raise TypeError("verify must be bool or path-like")

        if cert is not None:
            if isinstance(cert, tuple):
                if len(cert) != 2:
                    raise TypeError("cert tuple must be (certfile, keyfile)")
                certfile, keyfile = cert
                context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
            else:
                context.load_cert_chain(certfile=str(cert))

        return context

    def _build_transport_opener(
        self,
        allow_redirects: bool,
        verify: VerifyType,
        cert: CertType | None,
        proxies: ProxyType | None,
    ):
        handlers: list[Any] = [urllib.request.HTTPCookieProcessor(self.cookies)]

        proxy_map = self._normalize_proxy_map(proxies)
        if proxy_map is not None:
            handlers.append(urllib.request.ProxyHandler(proxy_map))

        context = self._build_ssl_context(verify=verify, cert=cert)
        if context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=context))

        if allow_redirects:
            handlers.append(_SafeRedirectHandler())
        else:
            handlers.append(_NoRedirectHandler())

        return urllib.request.build_opener(*handlers)

    def _build_url(
        self, url: str, params: dict[str, Any] | Sequence[tuple[str, Any]] | None = None
    ) -> str:
        if not params:
            return url

        split = urllib.parse.urlsplit(url)
        existing_pairs = urllib.parse.parse_qsl(split.query, keep_blank_values=True)
        new_query = urllib.parse.urlencode(params, doseq=True)
        new_pairs = urllib.parse.parse_qsl(new_query, keep_blank_values=True)
        merged_query = urllib.parse.urlencode(existing_pairs + new_pairs, doseq=True)
        return urllib.parse.urlunsplit(
            (split.scheme, split.netloc, split.path, merged_query, split.fragment)
        )

    @staticmethod
    def _normalize_response_hooks(
        hooks: Any | None,
    ) -> list[Callable[[Response, dict[str, Any]], Response | None]]:
        if hooks is None:
            return []
        response_hooks = hooks.get("response") if isinstance(hooks, dict) else hooks

        if response_hooks is None:
            return []
        if callable(response_hooks):
            return [response_hooks]
        if isinstance(response_hooks, (list, tuple)):
            normalized = []
            for hook in response_hooks:
                if not callable(hook):
                    raise TypeError("response hooks must be callables")
                normalized.append(hook)
            return normalized
        raise TypeError(
            "hooks must be a callable, list of callables, or {'response': ...}"
        )

    @classmethod
    def _run_response_hooks(
        cls,
        response: Response,
        *,
        hooks: Any | None,
        request_meta: dict[str, Any],
    ) -> Response:
        for hook in cls._normalize_response_hooks(hooks):
            maybe_response = hook(response, request_meta)
            if isinstance(maybe_response, Response):
                response = maybe_response
        return response

    @staticmethod
    def _encode_basic_auth(auth: tuple[str, str]) -> str:
        username, password = auth
        token = f"{username}:{password}".encode()
        return "Basic " + base64.b64encode(token).decode("ascii")

    @staticmethod
    def _format_cookie_header(cookies: dict[str, Any]) -> str:
        pairs = [f"{key}={value}" for key, value in cookies.items()]
        return "; ".join(pairs)

    @staticmethod
    def _read_file_payload(file_data: Any) -> bytes:
        payload = file_data.read() if hasattr(file_data, "read") else file_data

        if isinstance(payload, str):
            return payload.encode("utf-8")
        if isinstance(payload, (bytes, bytearray)):
            return bytes(payload)

        raise TypeError(f"Unsupported file payload type: {type(payload).__name__}")

    @staticmethod
    def _normalize_form_items(data: Any | None) -> list[tuple[str, str]]:
        if data is None:
            return []
        if isinstance(data, dict):
            iterator = data.items()
        elif isinstance(data, (list, tuple)):
            iterator = data
        else:
            raise TypeError("multipart form fields must be dict or sequence of tuples")

        items: list[tuple[str, str]] = []
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
        data: Any | None,
        files: dict[str, Any],
        req_headers: dict[str, str],
    ) -> bytes:
        boundary = f"easyget-{uuid4().hex}"
        lines: list[bytes] = []

        def _header_safe(value: str) -> str:
            # Strip characters that could break or inject MIME headers.
            return "".join(c for c in str(value) if c not in '"\r\n')

        for key, value in cls._normalize_form_items(data):
            lines.append(f"--{boundary}\r\n".encode())
            lines.append(
                f'Content-Disposition: form-data; name="{_header_safe(key)}"\r\n\r\n'.encode()
            )
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
                    raise TypeError(
                        "file tuple must be (filename, data) or (filename, data, content_type)"
                    )
            elif hasattr(raw_value, "name"):
                filename = os.path.basename(raw_value.name) or field_name

            if content_type == "application/octet-stream":
                guessed = mimetypes.guess_type(str(filename))[0]
                if guessed:
                    content_type = guessed

            payload = cls._read_file_payload(file_value)
            safe_name = _header_safe(filename)

            lines.append(f"--{boundary}\r\n".encode())
            lines.append(
                f'Content-Disposition: form-data; name="{_header_safe(field_name)}"; filename="{safe_name}"\r\n'.encode()
            )
            lines.append(f"Content-Type: {content_type}\r\n\r\n".encode())
            lines.append(payload)
            lines.append(b"\r\n")

        lines.append(f"--{boundary}--\r\n".encode())
        req_headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        return b"".join(lines)

    @staticmethod
    def _split_timeout(timeout: TimeoutType) -> tuple[float | None, float | None]:
        """Split a timeout into (connect_timeout, read_timeout) pair."""
        if isinstance(timeout, tuple):
            if len(timeout) != 2:
                raise ValueError(
                    "timeout tuple must be (connect_timeout, read_timeout)"
                )
            connect_timeout, read_timeout = timeout
            return (
                float(connect_timeout) if connect_timeout is not None else None,
                float(read_timeout) if read_timeout is not None else None,
            )
        if timeout is None:
            return (None, None)
        return (float(timeout), float(timeout))

    @staticmethod
    def _apply_read_timeout(resp: Any, read_timeout: float) -> None:
        """Best-effort read timeout on the underlying socket after connect."""
        try:
            fp = getattr(resp, "fp", None)
            raw = getattr(fp, "raw", None)
            sock = getattr(raw, "_sock", None)
            if sock is not None:
                sock.settimeout(read_timeout)
        except Exception:
            pass

    @staticmethod
    def _normalize_data(
        data: Any | None,
        json: Any | None,
        files: dict[str, Any] | None,
        req_headers: dict[str, str],
    ) -> bytes | bytearray | None:
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

    def request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | Sequence[tuple[str, Any]] | None = None,
        data: Any | None = None,
        json: Any | None = None,
        files: dict[str, Any] | None = None,
        auth: tuple[str, str] | None = None,
        cookies: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: TimeoutType = 30,
        stream: bool = False,
        allow_redirects: bool = True,
        verify: VerifyType | None = None,
        cert: CertType | None = None,
        proxies: ProxyType | None = None,
        compressed: bool = False,
        hooks: ResponseHookType | None = None,
    ) -> Response:
        method = method.upper()
        _check_url_scheme(url)
        url = self._build_url(url, params=params)

        req_headers = self.headers.copy()
        if headers:
            req_headers.update(headers)
        if compressed:
            req_headers.setdefault("Accept-Encoding", "gzip, deflate")
        if auth is not None:
            req_headers["Authorization"] = self._encode_basic_auth(auth)
        if cookies:
            req_headers["Cookie"] = self._format_cookie_header(cookies)
        req_data = self._normalize_data(data, json, files, req_headers)
        connect_timeout, read_timeout = self._split_timeout(timeout)
        req = urllib.request.Request(  # noqa: S310
            url, data=req_data, headers=req_headers, method=method
        )
        resolved_verify = self._default_verify if verify is None else verify
        resolved_cert = self._default_cert if cert is None else cert
        resolved_proxies = self._default_proxies if proxies is None else proxies
        use_default_transport = (
            resolved_verify == self._default_verify
            and resolved_cert == self._default_cert
            and resolved_proxies == self._default_proxies
        )
        if use_default_transport:
            opener = self._opener if allow_redirects else self._opener_no_redirect
        else:
            opener = self._build_transport_opener(
                allow_redirects=allow_redirects,
                verify=resolved_verify,
                cert=resolved_cert,
                proxies=resolved_proxies,
            )

        try:
            # We must be careful with 'with' if we want to stream
            resp = opener.open(
                req,
                timeout=connect_timeout
                if connect_timeout is not None
                else read_timeout,
            )
            if read_timeout is not None:
                self._apply_read_timeout(resp, read_timeout)
            response = Response(
                status_code=resp.status, headers=dict(resp.headers), url=url
            )
            response.set_auto_decompress(compressed)

            if stream:
                response._stream_response = resp
                self._open_responses.add(response)
                response.add_close_callback(
                    lambda: self._open_responses.discard(response)
                )
            else:
                with resp:
                    response._content = resp.read()
            return self._run_response_hooks(
                response,
                hooks=hooks,
                request_meta={
                    "method": method,
                    "url": url,
                    "status_code": response.status_code,
                },
            )
        except urllib.error.HTTPError as e:
            # Even on error, we might want the response object
            if read_timeout is not None:
                self._apply_read_timeout(e, read_timeout)
            response = Response(status_code=e.code, headers=dict(e.headers), url=url)
            response.set_auto_decompress(compressed)
            if stream:
                response._stream_response = e
                self._open_responses.add(response)
                response.add_close_callback(
                    lambda: self._open_responses.discard(response)
                )
            else:
                response._content = e.read()
            return self._run_response_hooks(
                response,
                hooks=hooks,
                request_meta={
                    "method": method,
                    "url": url,
                    "status_code": response.status_code,
                },
            )
        except urllib.error.URLError as e:
            raise RequestError(
                f"Request failed: {e}",
                hint="Check network connectivity, DNS, proxy, and TLS settings.",
                context={
                    "url": url,
                    "reason": str(e.reason) if hasattr(e, "reason") else str(e),
                },
            )

    def get(self, url: str, **kwargs) -> Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, data: Any = None, **kwargs) -> Response:
        return self.request("POST", url, data=data, **kwargs)

    def head(self, url: str, **kwargs) -> Response:
        return self.request("HEAD", url, **kwargs)

    def put(self, url: str, data: Any = None, **kwargs) -> Response:
        return self.request("PUT", url, data=data, **kwargs)

    def patch(self, url: str, data: Any = None, **kwargs) -> Response:
        return self.request("PATCH", url, data=data, **kwargs)

    def delete(self, url: str, **kwargs) -> Response:
        return self.request("DELETE", url, **kwargs)

    def options(self, url: str, **kwargs) -> Response:
        return self.request("OPTIONS", url, **kwargs)

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
