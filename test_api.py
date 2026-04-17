import unittest
from unittest.mock import MagicMock, patch
import easyget

class TestAPI(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_get_simple(self, mock_urlopen):
        # Mock response
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {'Content-Type': 'text/plain'}
        mock_resp.read.return_value = b"Hello World"
        mock_urlopen.return_value = mock_resp
        
        resp = easyget.get("http://example.com")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.text, "Hello World")
        self.assertEqual(resp.headers['Content-Type'], 'text/plain')

    @patch("urllib.request.urlopen")
    def test_response_json(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {}
        mock_resp.read.return_value = b'{"key": "value"}'
        mock_urlopen.return_value = mock_resp
        
        resp = easyget.get("http://example.com")
        self.assertEqual(resp.json(), {"key": "value"})

    def test_session_headers(self):
        s = easyget.Session()
        s.headers['X-Test'] = 'TestValue'
        self.assertEqual(s.headers['X-Test'], 'TestValue')

if __name__ == "__main__":
    unittest.main()
