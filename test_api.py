import unittest
import io
from unittest.mock import MagicMock, patch
import easyget
from urllib.parse import urlsplit, parse_qs

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

    @patch("urllib.request.urlopen")
    def test_query_params_merge(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {}
        mock_resp.read.return_value = b"ok"
        mock_urlopen.return_value = mock_resp

        s = easyget.Session()
        s.get("http://example.com/path?x=1", params={"y": "2"})

        req = mock_urlopen.call_args.args[0]
        parsed = urlsplit(req.full_url)
        self.assertEqual(parsed.path, "/path")
        self.assertEqual(parse_qs(parsed.query), {"x": ["1"], "y": ["2"]})

    @patch("urllib.request.urlopen")
    def test_post_dict_body_is_form_encoded(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {}
        mock_resp.read.return_value = b"ok"
        mock_urlopen.return_value = mock_resp

        s = easyget.Session()
        s.post("http://example.com/form", data={"a": "1", "b": "2"})

        req = mock_urlopen.call_args.args[0]
        body = req.data.decode("utf-8")
        self.assertEqual(parse_qs(body), {"a": ["1"], "b": ["2"]})

    def test_response_text_respects_charset(self):
        response = easyget.Response(
            status_code=200,
            headers={"Content-Type": "text/plain; charset=latin-1"},
            url="http://example.com",
        )
        response._content = "café".encode("latin-1")
        self.assertEqual(response.text, "café")

    def test_stream_iter_preserves_content(self):
        response = easyget.Response(status_code=200, headers={}, url="http://example.com")
        response._stream_response = io.BytesIO(b"abcdef")

        self.assertEqual(list(response.iter_bytes(2)), [b"ab", b"cd", b"ef"])
        self.assertEqual(response.content, b"abcdef")

if __name__ == "__main__":
    unittest.main()
