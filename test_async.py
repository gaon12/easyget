import asyncio
import io
import unittest
from unittest.mock import MagicMock

import easyget


class TestAsyncAPI(unittest.TestCase):
    def test_async_session_get(self):
        async def run():
            sync_session = MagicMock()
            response = easyget.Response(status_code=200, headers={}, url="http://example.com")
            response._content = b"hello"
            sync_session.request.return_value = response

            async with easyget.AsyncSession(session=sync_session) as session:
                resp = await session.get("http://example.com")
                body = await resp.read()
                self.assertEqual(body, b"hello")
                self.assertEqual(resp.status, 200)

            sync_session.request.assert_called_once()
            sync_session.close.assert_called_once()

        asyncio.run(run())

    def test_async_response_iter_bytes(self):
        async def run():
            response = easyget.Response(status_code=200, headers={}, url="http://example.com")
            response._stream_response = io.BytesIO(b"abcdef")
            async_response = easyget.AsyncResponse(response)

            chunks = []
            async for chunk in async_response.iter_bytes(2):
                chunks.append(chunk)

            self.assertEqual(chunks, [b"ab", b"cd", b"ef"])
            self.assertEqual(await async_response.read(), b"abcdef")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
