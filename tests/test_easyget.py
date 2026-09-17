import importlib
import io
import json
import os
import re
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, mock_open, patch

from easyget.downloader import _compute_retry_delay, download_file, download_range
from easyget.exceptions import DownloadError, IntegrityError, RequestError
from easyget.input_parser import parse_file_list
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
        # Freeze the clock so slots are fully deterministic.
        with (
            patch("time.sleep") as mock_sleep,
            patch("time.monotonic", return_value=1000.0),
        ):
            limiter = SpeedLimiter(100)  # 100 bytes/sec
            limiter.wait(50)  # slot at t=1000.0 — already arrived, no sleep
            mock_sleep.assert_not_called()
            limiter.wait(50)  # next slot at t=1000.5 → sleep 0.5s
            args, _ = mock_sleep.call_args
            self.assertAlmostEqual(args[0], 0.5, places=5)

    def test_speed_limiter_serializes_slots_across_calls(self):
        # Every chunk reserves the next free slot, so concurrent callers share
        # one rate budget instead of each sleeping off the combined debt.
        with (
            patch("time.sleep") as mock_sleep,
            patch("time.monotonic", return_value=1000.0),
        ):
            limiter = SpeedLimiter(100)
            limiter.wait(10)  # slot 1000.0 → no wait
            limiter.wait(10)  # slot 1000.1 → sleep 0.1
            limiter.wait(10)  # slot 1000.2 → sleep 0.2
            delays = [call.args[0] for call in mock_sleep.call_args_list]
            self.assertEqual(len(delays), 2)
            self.assertAlmostEqual(delays[0], 0.1, places=5)
            self.assertAlmostEqual(delays[1], 0.2, places=5)

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

    def test_filename_from_headers_strips_windows_forbidden_chars(self):
        # ':' would create an NTFS alternate data stream on Windows.
        headers = {"Content-Disposition": 'attachment; filename="a:b.txt"'}
        self.assertEqual(
            get_filename_from_headers(headers, "http://example.com/x"),
            "ab.txt",
        )
        headers = {"Content-Disposition": 'attachment; filename="x<y>z|q?.txt"'}
        self.assertEqual(
            get_filename_from_headers(headers, "http://example.com/x"),
            "xyzq.txt",
        )

    def test_filename_from_headers_rfc5987_with_language_tag(self):
        headers = {
            "Content-Disposition": (
                "attachment; filename*=UTF-8'en'%ED%95%9C%EA%B8%80.zip"
            )
        }
        self.assertEqual(
            get_filename_from_headers(headers, "http://example.com/x"),
            "한글.zip",
        )

    def test_filename_from_url_percent_decodes(self):
        self.assertEqual(
            get_filename_from_url("http://example.com/%ED%95%9C%EA%B8%80.zip"),
            "한글.zip",
        )
        # Encoded separators cannot smuggle a path through the decode.
        self.assertEqual(
            get_filename_from_url("http://example.com/dir/%2e%2e%2fpasswd"),
            "passwd",
        )

    def test_parse_file_list_sanitizes_csv_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "list.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                f.write("url,filename\n")
                f.write("http://example.com/a.zip,../../evil.exe\n")
                f.write("http://example.com/b.zip,normal.zip\n")

            entries = parse_file_list(csv_path)

        self.assertEqual(
            entries,
            [
                ("http://example.com/a.zip", "evil.exe"),
                ("http://example.com/b.zip", "normal.zip"),
            ],
        )

    def test_iter_bytes_wraps_transport_errors_as_request_error(self):
        class _Boom:
            def read(self, size=-1):
                raise ConnectionResetError("connection reset")

            def close(self):
                pass

        response = Response(
            status_code=200, headers={}, url="http://example.com/file.txt"
        )
        response._stream_response = _Boom()

        with self.assertRaises(RequestError) as ctx:
            list(response.iter_bytes(1024))
        self.assertTrue(ctx.exception.retryable)

    @patch("easyget.downloader.Session")
    @patch("time.sleep")
    def test_download_retries_mid_body_connection_reset(
        self, mock_sleep, mock_session_cls
    ):
        class _Boom:
            def read(self, size=-1):
                raise ConnectionResetError("connection reset")

            def close(self):
                pass

        def _fresh_response(*_args, **_kwargs):
            # Each attempt needs an unconsumed stream — a real retry would
            # get a brand-new response object.
            resp = Response(
                status_code=200, headers={}, url="http://example.com/file.txt"
            )
            resp._stream_response = _Boom()
            return resp

        mock_session = MagicMock()
        mock_session.get.side_effect = _fresh_response
        mock_session_cls.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            output = f"{tmpdir}/out.txt"
            with self.assertRaises(DownloadError):
                download_file(
                    "http://example.com/file.txt",
                    output=output,
                    retries=2,
                    show_progress=False,
                )

        # 1 initial attempt + 2 retries — mid-body resets are transient.
        self.assertEqual(mock_session.get.call_count, 3)

    @patch("easyget.downloader.Session")
    def test_download_file_forwards_timeout(self, mock_session_cls):
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
                retries=0,
                show_progress=False,
                timeout=7.5,
            )

        self.assertEqual(mock_session.get.call_args.kwargs["timeout"], 7.5)

    @patch("easyget.downloader.Session")
    @patch("easyget.downloader.get_file_info")
    def test_timestamping_downloads_when_remote_is_newer(
        self, mock_get_file_info, mock_session_cls
    ):
        mock_get_file_info.return_value = (
            100,
            True,
            {"Last-Modified": "Wed, 21 Oct 2099 07:28:00 GMT"},
        )
        response = Response(
            status_code=200, headers={}, url="http://example.com/file.txt"
        )
        response._stream_response = io.BytesIO(b"fresh")
        mock_session = MagicMock()
        mock_session.get.return_value = response
        mock_session_cls.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            output = f"{tmpdir}/stale.txt"
            with open(output, "wb") as f:
                f.write(b"old")
            os.utime(output, (1000000000, 1000000000))

            info = download_file(
                "http://example.com/file.txt",
                output=output,
                timestamping=True,
                force=True,
                retries=0,
                show_progress=False,
            )

            self.assertFalse(info["skipped"])
            with open(output, "rb") as f:
                self.assertEqual(f.read(), b"fresh")

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

    @staticmethod
    def _range_serving_session(payload: bytes) -> MagicMock:
        """Mock Session whose get() answers Range requests with the right slice."""

        def _get(_url, headers=None, **_kwargs):
            match = re.fullmatch(r"bytes=(\d+)-(\d+)", (headers or {}).get("Range", ""))
            if not match:
                raise AssertionError(f"expected a Range header, got {headers!r}")
            start, end = int(match[1]), int(match[2])
            resp = Response(status_code=206, headers={}, url=_url)
            resp._stream_response = io.BytesIO(payload[start : end + 1])
            return resp

        session = MagicMock()
        session.get.side_effect = _get
        return session

    @staticmethod
    def _write_segment_meta(path: str, ranges: list[dict], size: int) -> None:
        meta = {
            "version": 1,
            "size": size,
            "etag": None,
            "last_modified": None,
            "ranges": ranges,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f)

    @patch("easyget.downloader.get_file_info")
    @patch("easyget.downloader.Session")
    def test_multithread_download_cleans_meta_on_success(
        self, mock_session_cls, mock_info
    ):
        payload = b"0123456789abcdefghij"  # 20 bytes → two 10-byte ranges
        mock_info.return_value = (len(payload), True, {})
        mock_session = self._range_serving_session(payload)
        mock_session_cls.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            output = f"{tmpdir}/out.bin"
            info = download_file(
                "http://example.com/f.bin",
                output=output,
                threads=2,
                retries=0,
                show_progress=False,
            )

            with open(output, "rb") as f:
                self.assertEqual(f.read(), payload)
            self.assertEqual(info["bytes"], len(payload))
            # The .part and its .meta sidecar must not outlive a clean finish.
            self.assertFalse(os.path.exists(output + ".part"))
            self.assertFalse(os.path.exists(output + ".part.meta"))

    @patch("easyget.downloader.get_file_info")
    @patch("easyget.downloader.Session")
    def test_multithread_resume_fetches_only_missing_segments(
        self, mock_session_cls, mock_info
    ):
        payload = b"0123456789"
        mock_info.return_value = (len(payload), True, {})
        mock_session = self._range_serving_session(payload)
        mock_session_cls.return_value = mock_session

        with tempfile.TemporaryDirectory() as tmpdir:
            output = f"{tmpdir}/out.bin"
            # Simulate an interrupted segmented download: a preallocated .part
            # whose first half holds real bytes and whose second half is zeros.
            with open(output + ".part", "wb") as f:
                f.truncate(len(payload))
            with open(output + ".part", "r+b") as f:
                f.write(payload[:5])
            self._write_segment_meta(
                output + ".part.meta",
                ranges=[
                    {"start": 0, "end": 4, "done": True},
                    {"start": 5, "end": 9, "done": False},
                ],
                size=len(payload),
            )

            info = download_file(
                "http://example.com/f.bin",
                output=output,
                threads=2,
                resume=True,
                retries=0,
                show_progress=False,
            )

            with open(output, "rb") as f:
                self.assertEqual(f.read(), payload)
            self.assertFalse(info["skipped"])
            # Only the pending range was requested.
            self.assertEqual(mock_session.get.call_count, 1)
            sent_headers = mock_session.get.call_args.kwargs.get(
                "headers"
            ) or mock_session.get.call_args[1].get("headers")
            self.assertEqual(sent_headers["Range"], "bytes=5-9")
            self.assertFalse(os.path.exists(output + ".part.meta"))

    @patch("easyget.downloader.get_file_info")
    @patch("easyget.downloader.Session")
    def test_fullsize_part_with_pending_meta_is_not_renamed(
        self, mock_session_cls, mock_info
    ):
        # Regression: a .part preallocated to total_size used to be mistaken
        # for a finished download on resume and renamed over the output.
        payload = b"0123456789"
        mock_info.return_value = (len(payload), True, {})
        mock_session_cls.return_value = self._range_serving_session(payload)

        with tempfile.TemporaryDirectory() as tmpdir:
            output = f"{tmpdir}/out.bin"
            with open(output + ".part", "wb") as f:
                f.truncate(len(payload))  # all holes, nothing written
            self._write_segment_meta(
                output + ".part.meta",
                ranges=[
                    {"start": 0, "end": 4, "done": False},
                    {"start": 5, "end": 9, "done": False},
                ],
                size=len(payload),
            )

            info = download_file(
                "http://example.com/f.bin",
                output=output,
                threads=2,
                resume=True,
                retries=0,
                show_progress=False,
            )

            self.assertFalse(info["skipped"])
            with open(output, "rb") as f:
                self.assertEqual(f.read(), payload)

    @patch("easyget.downloader.get_file_info")
    @patch("easyget.downloader.Session")
    def test_stale_meta_restarts_segmented_download(self, mock_session_cls, mock_info):
        # Meta whose recorded size disagrees with the server is discarded and
        # the whole file is fetched fresh.
        payload = b"0123456789"
        mock_info.return_value = (len(payload), True, {})
        mock_session_cls.return_value = self._range_serving_session(payload)

        with tempfile.TemporaryDirectory() as tmpdir:
            output = f"{tmpdir}/out.bin"
            with open(output + ".part", "wb") as f:
                f.truncate(len(payload))
            self._write_segment_meta(
                output + ".part.meta",
                ranges=[{"start": 0, "end": 3, "done": True}],
                size=4,  # stale: server now reports 10
            )

            download_file(
                "http://example.com/f.bin",
                output=output,
                threads=2,
                resume=True,
                retries=0,
                show_progress=False,
            )

            with open(output, "rb") as f:
                self.assertEqual(f.read(), payload)

    def test_iter_bytes_prefers_readinto_streams(self):
        class _ReadintoOnly:
            def __init__(self, data: bytes):
                self._pos = 0
                self._data = data

            def readinto(self, buffer):
                chunk = self._data[self._pos : self._pos + len(buffer)]
                buffer[: len(chunk)] = chunk
                self._pos += len(chunk)
                return len(chunk)

            def close(self):
                pass

        response = Response(status_code=200, headers={}, url="http://example.com/f.bin")
        response._stream_response = _ReadintoOnly(b"abcdef")

        self.assertEqual(list(response.iter_bytes(2)), [b"ab", b"cd", b"ef"])

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
