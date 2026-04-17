import io
import json
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
