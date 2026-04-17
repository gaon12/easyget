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

    def test_parse_args_supports_continue_alias(self):
        with patch.object(sys, "argv", ["easyget", "--continue", "http://example.com/file.txt"]):
            args = cli.parse_args()
        self.assertTrue(args.resume)

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
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "UNEXPECTED_ERROR")
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
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "request")
        self.assertEqual(payload["result"]["status"], 200)
        self.assertEqual(payload["result"]["method"], "POST")

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
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["result"]["method"], "HEAD")
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
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["ok"])
        kwargs = mock_session.request.call_args.kwargs
        self.assertTrue(kwargs["allow_redirects"])
        self.assertEqual(kwargs["verify"], "/tmp/ca.pem")
        self.assertEqual(kwargs["cert"], ("/tmp/client.crt", "/tmp/client.key"))
        self.assertEqual(kwargs["proxies"], "http://proxy.local:8080")
        self.assertTrue(kwargs["compressed"])

    @patch("easyget.cli.Session")
    def test_request_mode_output_select_status(self, mock_session_cls):
        response = easyget.Response(status_code=204, headers={"X-Test": "v"}, url="https://example.com")
        response._content = b""
        mock_session = MagicMock()
        mock_session.request.return_value = response
        mock_session_cls.return_value.__enter__.return_value = mock_session

        out = io.StringIO()
        argv = ["easyget", "--json", "--output-select", "status", "https://example.com"]
        with patch.object(sys, "argv", argv), redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                cli.main()

        self.assertEqual(ctx.exception.code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["result"]["status"], 204)
        self.assertNotIn("headers", payload["result"])

    @patch("easyget.cli.Session")
    def test_request_mode_output_select_headers_text(self, mock_session_cls):
        response = easyget.Response(status_code=200, headers={"X-Test": "v"}, url="https://example.com")
        response._content = b"body"
        mock_session = MagicMock()
        mock_session.request.return_value = response
        mock_session_cls.return_value.__enter__.return_value = mock_session

        out = io.StringIO()
        argv = ["easyget", "--output-select", "headers", "https://example.com"]
        with patch.object(sys, "argv", argv), redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                cli.main()

        self.assertEqual(ctx.exception.code, 0)
        printed = out.getvalue()
        self.assertIn("HTTP 200", printed)
        self.assertIn("X-Test: v", printed)
        self.assertNotIn("body", printed)

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
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["result"]["method"], "POST")
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
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["result"]["method"], "POST")
        kwargs = mock_session.request.call_args.kwargs
        self.assertEqual(kwargs["method"], "POST")
        self.assertEqual(kwargs["data"], [("name", "demo")])
        self.assertIn("file", kwargs["files"])
        file_tuple = kwargs["files"]["file"]
        self.assertEqual(file_tuple[0], "upload.txt")
        self.assertEqual(file_tuple[1], b"file-body")
        self.assertEqual(file_tuple[2], "text/plain")

    @patch("easyget.cli.download_file", side_effect=RuntimeError("boom"))
    def test_ai_mode_compact_error_payload(self, _mock_download):
        out = io.StringIO()
        argv = ["easyget", "--ai", "http://example.com/file.txt"]
        with patch.object(sys, "argv", argv), redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                cli.main()

        self.assertEqual(ctx.exception.code, 1)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["ok"], 0)
        self.assertEqual(payload["e"]["c"], "UNEXPECTED_ERROR")

    @patch("easyget.cli.download_file")
    def test_download_mode_forwards_retry_and_timestamping(self, mock_download):
        out = io.StringIO()
        argv = [
            "easyget",
            "--json",
            "--retry-delay",
            "2.5",
            "--retry-max-delay",
            "9",
            "--retry-backoff",
            "linear",
            "--timestamping",
            "http://example.com/file.txt",
        ]
        with patch.object(sys, "argv", argv), redirect_stdout(out):
            cli.main()

        payload = json.loads(out.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "download")
        kwargs = mock_download.call_args.kwargs
        self.assertEqual(kwargs["retry_delay"], 2.5)
        self.assertEqual(kwargs["retry_max_delay"], 9.0)
        self.assertEqual(kwargs["retry_backoff"], "linear")
        self.assertTrue(kwargs["timestamping"])


if __name__ == "__main__":
    unittest.main()
