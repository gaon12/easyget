import io
import json
import sys
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


if __name__ == "__main__":
    unittest.main()
