import importlib
import io
import os
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, mock_open, patch

from easyget.downloader import _compute_retry_delay, download_file, download_range
from easyget.exceptions import DownloadError, IntegrityError
from easyget.models import Response
from easyget.utils import (
    _CONFIRMED_OVERWRITES,
    ProgressBar,
    SpeedLimiter,
    get_filename_from_headers,
    get_filename_from_url,
    parse_speed,
    safe_rename,
    should_download_output,
)
from easyget.wildcard import expand_wildcard_url


class TestEasyGet(unittest.TestCase):
    def test_cli_module_imports(self):
        module = importlib.import_module("easyget.cli")
        self.assertTrue(callable(getattr(module, "main", None)))

    @patch("easyget.wildcard.Session")
    def test_wildcard_expansion_matches_links(self, mock_session_cls):
        html = """
        <a href="a.zip">a.zip</a>
        <a href="b.txt">b.txt</a>
        <a href="/files/c.zip?token=1">c.zip</a>
        """
        response = Response(
            status_code=200, headers={}, url="http://example.com/files/"
        )
        response._content = html.encode("utf-8")
        mock_session = MagicMock()
        mock_session.get.return_value = response
        mock_session_cls.return_value = mock_session

        matches = expand_wildcard_url("http://example.com/files/*.zip", headers={})
        self.assertEqual(
            matches,
            [
                ("http://example.com/files/a.zip", "a.zip"),
                ("http://example.com/files/c.zip?token=1", "c.zip"),
            ],
        )

    def test_parse_speed(self):
        self.assertEqual(parse_speed("1M"), 1024 * 1024)
        self.assertEqual(parse_speed("500K"), 500 * 1024)
        self.assertIsNone(parse_speed("invalid"))

    @patch("easyget.downloader.Session")
    def test_download_range_checks_206(self, mock_session_cls):
        response = Response(status_code=200, headers={}, url="http://example.com")
        response._stream_response = io.BytesIO(b"abc")
        mock_session = MagicMock()
        mock_session.get.return_value = response
        mock_session_cls.return_value = mock_session

        error_event = threading.Event()
        pbar = MagicMock()

        with (
            patch("builtins.open", mock_open()),
            self.assertRaises(IntegrityError),
        ):
            download_range(
                "http://example.com", 0, 100, {}, "dummy.part", pbar, None, error_event
            )

        self.assertTrue(error_event.is_set())

    def test_speed_limiter(self):
        with patch("time.sleep") as mock_sleep:
            limiter = SpeedLimiter(100)  # 100 bytes/sec
            limiter.start_time = 1000.0
            with patch("time.monotonic", return_value=1000.1):
                limiter.wait(50)  # expected 0.5s. elapsed 0.1s. sleep 0.4s.
                args, _ = mock_sleep.call_args
                self.assertAlmostEqual(args[0], 0.4, places=5)

    def test_filename_from_url_uses_index_html_fallback(self):
        self.assertEqual(get_filename_from_url("http://example.com/dir/"), "index.html")
        self.assertEqual(get_filename_from_url("http://example.com"), "index.html")
        self.assertEqual(
            get_filename_from_url("http://example.com/a/b.zip?x=1"), "b.zip"
        )

    def test_filename_from_headers_supports_rfc5987_star(self):
        headers = {
            "Content-Disposition": (
                "attachment; filename*=UTF-8''%ED%95%9C%EA%B8%80.zip"
            )
        }
        self.assertEqual(
            get_filename_from_headers(headers, "http://example.com/x"), "한글.zip"
        )

    def test_filename_from_headers_strips_path_traversal(self):
        headers = {"Content-Disposition": 'attachment; filename="../../etc/passwd"'}
        self.assertEqual(
            get_filename_from_headers(headers, "http://example.com/x"), "passwd"
        )
        headers = {"Content-Disposition": 'attachment; filename="..\\..\\evil.exe"'}
        self.assertEqual(
            get_filename_from_headers(headers, "http://example.com/x"), "evil.exe"
        )

    def test_filename_from_headers_stops_at_semicolon(self):
        headers = {"Content-Disposition": "attachment; filename=report.zip; other=x"}
        self.assertEqual(
            get_filename_from_headers(headers, "http://example.com/x"), "report.zip"
        )

    def test_filename_from_headers_rejects_reserved_and_control_names(self):
        headers = {"Content-Disposition": 'attachment; filename="CON"'}
        self.assertEqual(
            get_filename_from_headers(headers, "http://example.com/safe.txt"),
            "safe.txt",
        )
        headers = {"Content-Disposition": 'attachment; filename="bad\r\nname.txt"'}
        self.assertEqual(
            get_filename_from_headers(headers, "http://example.com/safe.txt"),
            "safe.txt",
        )

    @patch("os.remove")
    @patch("os.replace")
    @patch("os.path.exists", return_value=True)
    @patch("sys.stdin.isatty", return_value=False)
    def test_safe_rename_non_interactive_skips(
        self, mock_tty, mock_exists, mock_replace, mock_remove
    ):
        result = safe_rename("tmp.part", "out.txt")
        self.assertFalse(result)
        mock_remove.assert_called_once_with("tmp.part")

    @patch("builtins.input", return_value="y")
    @patch("sys.stdin.isatty", return_value=True)
    @patch("os.path.exists", return_value=True)
    def test_overwrite_confirmed_once_per_path(self, mock_exists, mock_tty, mock_input):
        output_key = os.path.abspath("out.txt")
        _CONFIRMED_OVERWRITES.discard(output_key)
        try:
            self.assertTrue(should_download_output("out.txt"))
            self.assertTrue(should_download_output("out.txt"))
            mock_input.assert_called_once()
        finally:
            _CONFIRMED_OVERWRITES.discard(output_key)

    @patch("easyget.downloader.get_file_info", return_value=(10, True, {}))
    @patch("easyget.downloader.Session")
    @patch("time.sleep")
    def test_resume_unsupported_fails_without_retry(
        self, mock_sleep, mock_session_cls, mock_info
    ):
        response = Response(
            status_code=200, headers={}, url="http://example.com/file.txt"
        )
        response._stream_response = io.BytesIO(b"abcdefghij")
        mock_session = MagicMock()
        mock_session.get.return_value = response
        mock_session_cls.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            output = f"{tmpdir}/out.txt"
            with open(output + ".part", "wb") as f:
                f.write(b"abc")

            with self.assertRaises(IntegrityError):
                download_file(
                    "http://example.com/file.txt",
                    output=output,
                    resume=True,
                    retries=5,
                    show_progress=False,
                )

        self.assertEqual(mock_session.get.call_count, 1)
        mock_sleep.assert_not_called()

    def test_progress_bar_logic(self):
        # Ensure it doesn't crash
        pbar = ProgressBar(1000, desc="Test")
        pbar.update(500)
        pbar.close()

    def test_retry_delay_strategy(self):
        self.assertEqual(
            _compute_retry_delay(
                1, retry_delay=1.0, retry_backoff="fixed", retry_max_delay=10.0
            ),
            1.0,
        )
        self.assertEqual(
            _compute_retry_delay(
                3, retry_delay=1.0, retry_backoff="linear", retry_max_delay=10.0
            ),
            3.0,
        )
        self.assertEqual(
            _compute_retry_delay(
                3, retry_delay=1.0, retry_backoff="exponential", retry_max_delay=10.0
            ),
            4.0,
        )
        self.assertEqual(
            _compute_retry_delay(
                10, retry_delay=2.0, retry_backoff="exponential", retry_max_delay=5.0
            ),
            5.0,
        )

    @patch("easyget.downloader.Session")
    def test_download_file_raises_on_http_error(self, mock_session_cls):
        response = Response(
            status_code=404, headers={}, url="http://example.com/file.txt"
        )
        response._stream_response = io.BytesIO(b"not found")
        mock_session = MagicMock()
        mock_session.get.return_value = response
        mock_session_cls.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            output = f"{tmpdir}/out.txt"
            with self.assertRaises(DownloadError):
                download_file(
                    "http://example.com/file.txt",
                    output=output,
                    retries=0,
                    show_progress=False,
                )

    @patch("easyget.downloader.Session")
    def test_skip_existing_avoids_network_request(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            output = f"{tmpdir}/already.txt"
            with open(output, "wb") as f:
                f.write(b"existing")

            download_file(
                "http://example.com/file.txt",
                output=output,
                skip_existing=True,
                retries=0,
                show_progress=False,
            )

        mock_session.get.assert_not_called()

    @patch(
        "easyget.downloader.get_file_info",
        side_effect=AssertionError("fast mode should not probe metadata"),
    )
    @patch("easyget.downloader.Session")
    def test_fast_mode_skips_file_info_probe(
        self, mock_session_cls, mock_get_file_info
    ):
        response = Response(
            status_code=200, headers={}, url="http://example.com/file.txt"
        )
        response._stream_response = io.BytesIO(b"abc")
        mock_session = MagicMock()
        mock_session.get.return_value = response
        mock_session_cls.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            output = f"{tmpdir}/saved.txt"
            download_file(
                "http://example.com/file.txt",
                output=output,
                mode="fast",
                retries=0,
                show_progress=False,
            )
            with open(output, "rb") as f:
                self.assertEqual(f.read(), b"abc")

        mock_get_file_info.assert_not_called()

    @patch("easyget.downloader.Session")
    @patch("easyget.downloader.get_file_info")
    def test_timestamping_skips_when_local_is_newer(
        self, mock_get_file_info, mock_session_cls
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_get_file_info.return_value = (
            100,
            True,
            {"Last-Modified": "Wed, 21 Oct 2015 07:28:00 GMT"},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output = f"{tmpdir}/exists.txt"
            with open(output, "wb") as f:
                f.write(b"local")
            # Ensure local file mtime is newer than mocked remote date.
            os.utime(output, (1700000000, 1700000000))

            download_file(
                "http://example.com/file.txt",
                output=output,
                timestamping=True,
                retries=0,
                show_progress=False,
            )

        mock_session.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
