"""
Integration tests using a real localhost HTTP server.

Unlike the mock-based unit tests, these exercise the actual urllib
transport path end-to-end, including redirect handling.
"""

import http.server
import os
import tempfile
import threading
import time
import unittest
from urllib.parse import urlsplit

import easyget
from easyget.downloader import download_file
from easyget.exceptions import DownloadError, RequestError


def _with_retries(fn, attempts=8):
    """Retry a callable on transient transport failures.

    The Windows loopback stack on this host intermittently answers localhost
    connections with RST (observed ~20% of requests against a bare
    ThreadingHTTPServer via raw urllib). That environmental flake must not
    mask the redirect behaviour under test, so retry retryable transport
    errors. Non-retryable errors (e.g. a refused redirect) propagate.
    """
    last = None
    for _ in range(attempts):
        try:
            return fn()
        except (RequestError, DownloadError, OSError) as e:
            last = e
            if not getattr(e, "retryable", True):
                raise
    raise last


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "easyget-test/1.0"
    # HTTP/1.0: every response ends with a server-initiated FIN. With 1.1
    # keep-alive, urllib abandons redirected sockets and Windows can deliver
    # a RST that kills a later request mid-flight.
    protocol_version = "HTTP/1.0"

    # Set by tests on the server class via make_server().
    auth_seen: dict[str, str | None] = {}

    def log_message(self, *args):  # noqa: D102 - silence test server logs
        pass

    def _send_bytes(self, body: bytes, headers: dict[str, str] | None = None):
        self.send_response(200)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command == "HEAD":
            return
        # Write in chunks and let the socket settle before close — on
        # Windows, closing a socket right after a large send can RST the
        # connection and discard the buffered payload before the client
        # reads it.
        for i in range(0, len(body), 65536):
            self.wfile.write(body[i : i + 65536])
        self.wfile.flush()
        if len(body) > 1024 * 1024:
            time.sleep(0.3)

    def do_GET(self):
        self.auth_seen["Authorization"] = self.headers.get("Authorization")
        self.auth_seen["Cookie"] = self.headers.get("Cookie")
        path = urlsplit(self.path).path

        if path == "/file":
            self._send_bytes(
                b"real file bytes",
                {"Content-Disposition": 'attachment; filename="real.bin"'},
            )
        elif path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/file")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/chain1":
            self.send_response(302)
            self.send_header("Location", "/redirect")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/to-file":
            self.send_response(302)
            self.send_header("Location", "file:///etc/passwd")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/redirect-big":
            self.send_response(302)
            self.send_header("Location", "/big")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/big":
            # ~8 MiB generated body — exercises the chunked download path
            # after a redirect without shipping a huge fixture.
            body = b"0123456789abcdef" * (8 * 1024 * 1024 // 16)
            self._send_bytes(
                body, {"Content-Disposition": 'attachment; filename="big.bin"'}
            )
        elif path == "/cross":
            other_port = self.server.other_port
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{other_port}/file")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_HEAD(self):
        self.do_GET()


def _make_server():
    # ThreadingHTTPServer: HTTP/1.1 keep-alive would otherwise let one
    # connection starve every subsequent request on a single-threaded server.
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class TestRedirectIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _make_server()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_download_follows_redirect(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "out.bin")
            info = _with_retries(
                lambda: download_file(
                    f"http://127.0.0.1:{self.port}/redirect",
                    output=output,
                    retries=0,
                    show_progress=False,
                )
            )
            with open(output, "rb") as f:
                self.assertEqual(f.read(), b"real file bytes")
            self.assertEqual(info["bytes"], len(b"real file bytes"))

    def test_download_follows_redirect_chain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "out.bin")
            _with_retries(
                lambda: download_file(
                    f"http://127.0.0.1:{self.port}/chain1",
                    output=output,
                    retries=0,
                    show_progress=False,
                )
            )
            with open(output, "rb") as f:
                self.assertEqual(f.read(), b"real file bytes")

    def test_redirect_to_file_scheme_refused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "out.bin")
            with self.assertRaises(RequestError):
                _with_retries(
                    lambda: download_file(
                        f"http://127.0.0.1:{self.port}/to-file",
                        output=output,
                        retries=0,
                        show_progress=False,
                    )
                )
            self.assertFalse(os.path.exists(output))

    def test_cross_origin_redirect_strips_authorization(self):
        # Second server acts as the "different origin" redirect target.
        other = _make_server()
        try:
            self.server.other_port = other.server_address[1]
            _Handler.auth_seen = {}
            resp = _with_retries(
                lambda: easyget.get(
                    f"http://127.0.0.1:{self.port}/cross",
                    headers={"Authorization": "Basic dXNlcjpwYXNz"},
                )
            )
            self.assertEqual(resp.content, b"real file bytes")
            # The target server must NOT have seen the credential.
            self.assertIsNone(_Handler.auth_seen.get("Authorization"))
        finally:
            other.shutdown()
            other.server_close()

    def test_same_origin_redirect_keeps_authorization(self):
        _Handler.auth_seen = {}
        resp = _with_retries(
            lambda: easyget.get(
                f"http://127.0.0.1:{self.port}/redirect",
                headers={"Authorization": "Basic dXNlcjpwYXNz"},
            )
        )
        self.assertEqual(resp.content, b"real file bytes")
        self.assertEqual(_Handler.auth_seen.get("Authorization"), "Basic dXNlcjpwYXNz")

    def test_download_large_via_redirect(self):
        # Mimics https://link.testfile.org/500MB — an entry URL that 302s to
        # a large payload. 8 MiB is enough to exercise chunked streaming.
        expected = 8 * 1024 * 1024
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "big.bin")
            info = _with_retries(
                lambda: download_file(
                    f"http://127.0.0.1:{self.port}/redirect-big",
                    output=output,
                    retries=0,
                    show_progress=False,
                )
            )
            self.assertEqual(info["bytes"], expected)
            self.assertEqual(os.path.getsize(output), expected)

    def test_get_with_params_via_redirect(self):
        # request() with params through a redirect — exercises _build_url
        # before the redirect hop.
        resp = _with_retries(
            lambda: easyget.get(
                f"http://127.0.0.1:{self.port}/redirect", params={"q": "1"}
            )
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"real file bytes")


if __name__ == "__main__":
    unittest.main()
