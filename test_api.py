import io
import json
import base64
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import easyget


def make_http_response(status=200, headers=None, body=b"ok"):
    response = MagicMock()
    response.status = status
    response.headers = headers or {}
    response.read.return_value = body
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class TestAPI(unittest.TestCase):
    def test_get_simple(self):
        response = make_http_response(
            status=200,
            headers={"Content-Type": "text/plain"},
            body=b"Hello World",
        )
        opener = MagicMock()
        opener.open.return_value = response
        opener_no_redirect = MagicMock()
        opener_no_redirect.open.return_value = response

        with patch("easyget.session.urllib.request.build_opener", side_effect=[opener, opener_no_redirect]):
            resp = easyget.get("http://example.com")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.text, "Hello World")
        self.assertEqual(resp.headers["Content-Type"], "text/plain")
        self.assertTrue(resp.ok)

    def test_response_json(self):
        response = make_http_response(status=200, body=b'{"key": "value"}')
        opener = MagicMock()
        opener.open.return_value = response
        opener_no_redirect = MagicMock()
        opener_no_redirect.open.return_value = response

        with patch("easyget.session.urllib.request.build_opener", side_effect=[opener, opener_no_redirect]):
            resp = easyget.get("http://example.com")

        self.assertEqual(resp.json(), {"key": "value"})

    def test_session_headers(self):
        s = easyget.Session()
        s.headers["X-Test"] = "TestValue"
        self.assertEqual(s.headers["X-Test"], "TestValue")

    def test_query_params_merge(self):
        response = make_http_response(status=200)
        opener = MagicMock()
        opener.open.return_value = response
        opener_no_redirect = MagicMock()
        opener_no_redirect.open.return_value = response

        with patch("easyget.session.urllib.request.build_opener", side_effect=[opener, opener_no_redirect]):
            s = easyget.Session()
            s.get("http://example.com/path?x=1", params={"y": "2"})

        req = opener.open.call_args.args[0]
        parsed = urlsplit(req.full_url)
        self.assertEqual(parsed.path, "/path")
        self.assertEqual(parse_qs(parsed.query), {"x": ["1"], "y": ["2"]})

    def test_post_dict_body_is_form_encoded(self):
        response = make_http_response(status=200)
        opener = MagicMock()
        opener.open.return_value = response
        opener_no_redirect = MagicMock()
        opener_no_redirect.open.return_value = response

        with patch("easyget.session.urllib.request.build_opener", side_effect=[opener, opener_no_redirect]):
            s = easyget.Session()
            s.post("http://example.com/form", data={"a": "1", "b": "2"})

        req = opener.open.call_args.args[0]
        body = req.data.decode("utf-8")
        self.assertEqual(parse_qs(body), {"a": ["1"], "b": ["2"]})

    def test_post_sequence_body_is_form_encoded(self):
        response = make_http_response(status=200)
        opener = MagicMock()
        opener.open.return_value = response
        opener_no_redirect = MagicMock()
        opener_no_redirect.open.return_value = response

        with patch("easyget.session.urllib.request.build_opener", side_effect=[opener, opener_no_redirect]):
            s = easyget.Session()
            s.post("http://example.com/form", data=[("a", "1"), ("a", "2")])

        req = opener.open.call_args.args[0]
        body = req.data.decode("utf-8")
        self.assertEqual(parse_qs(body), {"a": ["1", "2"]})

    def test_post_json_body_is_encoded(self):
        response = make_http_response(status=200)
        opener = MagicMock()
        opener.open.return_value = response
        opener_no_redirect = MagicMock()
        opener_no_redirect.open.return_value = response

        with patch("easyget.session.urllib.request.build_opener", side_effect=[opener, opener_no_redirect]):
            s = easyget.Session()
            s.post("http://example.com/form", json={"a": 1})

        req = opener.open.call_args.args[0]
        self.assertEqual(json.loads(req.data.decode("utf-8")), {"a": 1})
        self.assertEqual(req.get_header("Content-type"), "application/json")

    def test_post_files_builds_multipart_body(self):
        response = make_http_response(status=200)
        opener = MagicMock()
        opener.open.return_value = response
        opener_no_redirect = MagicMock()
        opener_no_redirect.open.return_value = response

        with patch("easyget.session.urllib.request.build_opener", side_effect=[opener, opener_no_redirect]):
            s = easyget.Session()
            s.post(
                "http://example.com/upload",
                data={"name": "demo"},
                files={"file": ("hello.txt", b"hello world", "text/plain")},
            )

        req = opener.open.call_args.args[0]
        content_type = req.get_header("Content-type")
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        body = req.data
        self.assertIn(b'Content-Disposition: form-data; name="name"', body)
        self.assertIn(b'Content-Disposition: form-data; name="file"; filename="hello.txt"', body)
        self.assertIn(b"hello world", body)

    def test_auth_and_cookies_headers_are_applied(self):
        response = make_http_response(status=200)
        opener = MagicMock()
        opener.open.return_value = response
        opener_no_redirect = MagicMock()
        opener_no_redirect.open.return_value = response

        with patch("easyget.session.urllib.request.build_opener", side_effect=[opener, opener_no_redirect]):
            s = easyget.Session()
            s.get(
                "http://example.com/resource",
                auth=("user", "pass"),
                cookies={"sid": "abc", "mode": "test"},
            )

        req = opener.open.call_args.args[0]
        expected_auth = "Basic " + base64.b64encode(b"user:pass").decode("ascii")
        self.assertEqual(req.get_header("Authorization"), expected_auth)
        self.assertIn("sid=abc", req.get_header("Cookie"))
        self.assertIn("mode=test", req.get_header("Cookie"))

    def test_data_and_json_together_raise_type_error(self):
        response = make_http_response(status=200)
        opener = MagicMock()
        opener.open.return_value = response
        opener_no_redirect = MagicMock()
        opener_no_redirect.open.return_value = response

        with patch("easyget.session.urllib.request.build_opener", side_effect=[opener, opener_no_redirect]):
                s = easyget.Session()
                with self.assertRaises(TypeError):
                    s.post("http://example.com/form", data={"a": "1"}, json={"a": 1})

    def test_json_and_files_together_raise_type_error(self):
        response = make_http_response(status=200)
        opener = MagicMock()
        opener.open.return_value = response
        opener_no_redirect = MagicMock()
        opener_no_redirect.open.return_value = response

        with patch("easyget.session.urllib.request.build_opener", side_effect=[opener, opener_no_redirect]):
            s = easyget.Session()
            with self.assertRaises(TypeError):
                s.post("http://example.com/form", json={"a": 1}, files={"file": b"x"})

    def test_allow_redirects_false_uses_no_redirect_opener(self):
        response = make_http_response(status=302, headers={"Location": "http://example.com/next"})
        opener = MagicMock()
        opener.open.return_value = response
        opener_no_redirect = MagicMock()
        opener_no_redirect.open.return_value = response

        with patch("easyget.session.urllib.request.build_opener", side_effect=[opener, opener_no_redirect]):
            s = easyget.Session()
            s.get("http://example.com/start", allow_redirects=False)

        opener_no_redirect.open.assert_called_once()
        opener.open.assert_not_called()

    def test_timeout_tuple_uses_read_timeout(self):
        response = make_http_response(status=200)
        opener = MagicMock()
        opener.open.return_value = response
        opener_no_redirect = MagicMock()
        opener_no_redirect.open.return_value = response

        with patch("easyget.session.urllib.request.build_opener", side_effect=[opener, opener_no_redirect]):
            s = easyget.Session()
            s.get("http://example.com", timeout=(1.0, 2.5))

        self.assertEqual(opener.open.call_args.kwargs["timeout"], 2.5)

    def test_top_level_stream_request_lifetime(self):
        response = make_http_response(status=200, body=b"streamed")
        response.read.side_effect = [b"str", b"eam", b"ed", b""]
        opener = MagicMock()
        opener.open.return_value = response
        opener_no_redirect = MagicMock()
        opener_no_redirect.open.return_value = response

        with patch("easyget.session.urllib.request.build_opener", side_effect=[opener, opener_no_redirect]):
            resp = easyget.get("http://example.com", stream=True)
            self.assertFalse(resp.closed)
            self.assertEqual(next(resp.iter_bytes(3)), b"str")
            resp.close()
            self.assertTrue(resp.closed)

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
