import asyncio
import io
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

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

    def test_async_session_get_context_manager(self):
        async def run():
            sync_session = MagicMock()
            response = easyget.Response(status_code=200, headers={}, url="http://example.com")
            response._content = b"context"
            sync_session.request.return_value = response

            async with easyget.AsyncSession(session=sync_session) as session:
                async with session.get("http://example.com") as resp:
                    self.assertEqual(await resp.read(), b"context")

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

    def test_arequest_stream_keeps_session_until_aclose(self):
        async def run():
            mock_async_session = MagicMock()
            response = easyget.AsyncResponse(
                easyget.Response(status_code=200, headers={}, url="http://example.com")
            )
            response._response._stream_response = io.BytesIO(b"stream")

            mock_async_session.request.return_value = easyget.AsyncRequestContextManager(
                asyncio.sleep(0, result=response)
            )
            mock_async_session.close = AsyncMock()

            with patch("easyget.async_session.AsyncSession", return_value=mock_async_session):
                resp = await easyget.aget("http://example.com", stream=True)
                self.assertFalse(resp.closed)
                await resp.aclose()

            mock_async_session.close.assert_awaited_once()

        asyncio.run(run())

    def test_async_session_max_concurrency_validation(self):
        with self.assertRaises(ValueError):
            easyget.AsyncSession(max_concurrency=0)

    def test_async_session_forwards_transport_kwargs(self):
        async def run():
            sync_session = MagicMock()
            response = easyget.Response(status_code=200, headers={}, url="https://example.com")
            response._content = b"ok"
            sync_session.request.return_value = response

            async with easyget.AsyncSession(session=sync_session) as session:
                resp = await session.get(
                    "https://example.com",
                    verify=False,
                    cert=("client.crt", "client.key"),
                    proxies={"https": "http://proxy.local:8080"},
                    compressed=True,
                    timeout=(1, 2),
                )
                self.assertEqual(await resp.read(), b"ok")

            kwargs = sync_session.request.call_args.kwargs
            self.assertFalse(kwargs["verify"])
            self.assertEqual(kwargs["cert"], ("client.crt", "client.key"))
            self.assertEqual(kwargs["proxies"], {"https": "http://proxy.local:8080"})
            self.assertTrue(kwargs["compressed"])
            self.assertEqual(kwargs["timeout"], (1, 2))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
