import unittest
from unittest.mock import MagicMock, patch, mock_open
import os
import threading
import urllib.request
import logging

from easyget.downloader import download_file, download_range
from easyget.utils import parse_speed, SpeedLimiter, safe_rename, ProgressBar

class TestEasyGet(unittest.TestCase):

    def test_parse_speed(self):
        self.assertEqual(parse_speed("1M"), 1024 * 1024)
        self.assertEqual(parse_speed("500K"), 500 * 1024)
        self.assertIsNone(parse_speed("invalid"))

    @patch("urllib.request.urlopen")
    def test_download_range_checks_206(self, mock_urlopen):
        # Mock response
        mock_response = MagicMock()
        mock_response.status = 200 # ERROR: should be 206
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        error_event = threading.Event()
        pbar = MagicMock()
        
        with patch("builtins.open", mock_open()):
            download_range("http://example.com", 0, 100, {}, "dummy.part", pbar, None, error_event)
        
        self.assertTrue(error_event.is_set())

    def test_speed_limiter(self):
        with patch("time.sleep") as mock_sleep:
            limiter = SpeedLimiter(100) # 100 bytes/sec
            limiter.start_time = 1000.0
            with patch("time.time", return_value=1000.1):
                limiter.wait(50) # expected 0.5s. elapsed 0.1s. sleep 0.4s.
                args, _ = mock_sleep.call_args
                self.assertAlmostEqual(args[0], 0.4, places=5)

    @patch("os.remove")
    @patch("os.replace")
    @patch("os.path.exists", return_value=True)
    @patch("sys.stdin.isatty", return_value=False)
    def test_safe_rename_non_interactive_skips(self, mock_tty, mock_exists, mock_replace, mock_remove):
        result = safe_rename("tmp.part", "out.txt")
        self.assertFalse(result)
        mock_remove.assert_called_once_with("tmp.part")

    def test_progress_bar_logic(self):
        # Ensure it doesn't crash
        pbar = ProgressBar(1000, desc="Test")
        pbar.update(500)
        pbar.close()

if __name__ == "__main__":
    unittest.main()
