import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import MagicMock, patch

import easyget
from easyget import cli


class TestCLI(unittest.TestCase):
    def test_parse_args_accepts_uppercase_o(self):
        with patch.object(sys, "argv", ["easyget", "-O", "saved.txt", "http://example.com/file.txt"]):
            args = cli.parse_args()
        self.assertEqual(args.output, "saved.txt")

    @patch("easyget.cli.download_file", side_effect=RuntimeError("boom"))
    def test_json_mode_outputs_structured_error(self, _mock_download):
        out = io.StringIO()
        err = io.StringIO()
        argv = ["easyget", "--json", "http://example.com/file.txt"]

        with patch.object(sys, "argv", argv), redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cli.main()

        self.assertEqual(ctx.exception.code, 1)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload[0]["status"], "error")
        self.assertEqual(err.getvalue().strip(), "")

    @patch("easyget.cli.Session")
    def test_request_mode_json_payload(self, mock_session_cls):
        response = easyget.Response(status_code=200, headers={"Content-Type": "application/json"}, url="http://example.com")
        response._content = b'{"ok": true}'
        mock_session = MagicMock()
        mock_session.request.return_value = response
        mock_session_cls.return_value.__enter__.return_value = mock_session

        out = io.StringIO()
        err = io.StringIO()
        argv = [
            "easyget",
            "--json",
            "-X",
            "POST",
            "--json-data",
            '{"name":"easyget"}',
            "http://example.com",
        ]
        with patch.object(sys, "argv", argv), redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cli.main()

        self.assertEqual(ctx.exception.code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], 200)
        self.assertEqual(payload["method"], "POST")

        kwargs = mock_session.request.call_args.kwargs
        self.assertEqual(kwargs["method"], "POST")
        self.assertEqual(kwargs["json"], {"name": "easyget"})
        self.assertEqual(kwargs["allow_redirects"], False)

    @patch("easyget.cli.Session")
    def test_request_mode_head_uses_head_method(self, mock_session_cls):
        response = easyget.Response(status_code=200, headers={}, url="http://example.com")
        response._content = b""
        mock_session = MagicMock()
        mock_session.request.return_value = response
        mock_session_cls.return_value.__enter__.return_value = mock_session

        out = io.StringIO()
        argv = ["easyget", "-I", "--json", "http://example.com"]
        with patch.object(sys, "argv", argv), redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                cli.main()

        self.assertEqual(ctx.exception.code, 0)
        kwargs = mock_session.request.call_args.kwargs
        self.assertEqual(kwargs["method"], "HEAD")

    @patch("easyget.cli.Session")
    def test_request_mode_transport_flags(self, mock_session_cls):
        response = easyget.Response(status_code=200, headers={}, url="https://example.com")
        response._content = b"ok"
        mock_session = MagicMock()
        mock_session.request.return_value = response
        mock_session_cls.return_value.__enter__.return_value = mock_session

        out = io.StringIO()
        argv = [
            "easyget",
            "--json",
            "-X",
            "GET",
            "-L",
            "--proxy",
            "http://proxy.local:8080",
            "--cacert",
            "/tmp/ca.pem",
            "--cert",
            "/tmp/client.crt",
            "--key",
            "/tmp/client.key",
            "--compressed",
            "https://example.com",
        ]
        with patch.object(sys, "argv", argv), redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                cli.main()

        self.assertEqual(ctx.exception.code, 0)
        kwargs = mock_session.request.call_args.kwargs
        self.assertTrue(kwargs["allow_redirects"])
        self.assertEqual(kwargs["verify"], "/tmp/ca.pem")
        self.assertEqual(kwargs["cert"], ("/tmp/client.crt", "/tmp/client.key"))
        self.assertEqual(kwargs["proxies"], "http://proxy.local:8080")
        self.assertTrue(kwargs["compressed"])

    @patch("easyget.cli.Session")
    def test_request_mode_data_urlencode_builds_body(self, mock_session_cls):
        response = easyget.Response(status_code=200, headers={}, url="https://example.com")
        response._content = b"ok"
        mock_session = MagicMock()
        mock_session.request.return_value = response
        mock_session_cls.return_value.__enter__.return_value = mock_session

        out = io.StringIO()
        argv = [
            "easyget",
            "--json",
            "--data-urlencode",
            "q=hello world",
            "--data-urlencode",
            "lang=ko",
            "https://example.com",
        ]
        with patch.object(sys, "argv", argv), redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                cli.main()

        self.assertEqual(ctx.exception.code, 0)
        kwargs = mock_session.request.call_args.kwargs
        self.assertEqual(kwargs["method"], "POST")
        self.assertEqual(kwargs["data"], "q=hello+world&lang=ko")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/x-www-form-urlencoded")

    @patch("easyget.cli.Session")
    def test_request_mode_form_parsing(self, mock_session_cls):
        response = easyget.Response(status_code=200, headers={}, url="https://example.com")
        response._content = b"ok"
        mock_session = MagicMock()
        mock_session.request.return_value = response
        mock_session_cls.return_value.__enter__.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "upload.txt")
            with open(file_path, "wb") as f:
                f.write(b"file-body")

            out = io.StringIO()
            argv = [
                "easyget",
                "--json",
                "-F",
                "name=demo",
                "-F",
                f"file=@{file_path};type=text/plain",
                "https://example.com",
            ]
            with patch.object(sys, "argv", argv), redirect_stdout(out):
                with self.assertRaises(SystemExit) as ctx:
                    cli.main()

        self.assertEqual(ctx.exception.code, 0)
        kwargs = mock_session.request.call_args.kwargs
        self.assertEqual(kwargs["method"], "POST")
        self.assertEqual(kwargs["data"], [("name", "demo")])
        self.assertIn("file", kwargs["files"])
        file_tuple = kwargs["files"]["file"]
        self.assertEqual(file_tuple[0], "upload.txt")
        self.assertEqual(file_tuple[1], b"file-body")
        self.assertEqual(file_tuple[2], "text/plain")


if __name__ == "__main__":
    unittest.main()
