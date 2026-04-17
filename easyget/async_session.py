import asyncio
from typing import Any, Optional

from .models import Response
from .session import Session


def _next_chunk(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return None


class AsyncResponse:
    def __init__(self, response: Response):
        self._response = response

    @property
    def status(self) -> int:
        return self._response.status

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def headers(self):
        return self._response.headers

    @property
    def url(self) -> str:
        return self._response.url

    @property
    def ok(self) -> bool:
        return self._response.ok

    async def read(self) -> bytes:
        return await asyncio.to_thread(lambda: self._response.content)

    async def text(self) -> str:
        return await asyncio.to_thread(lambda: self._response.text)

    async def json(self):
        return await asyncio.to_thread(self._response.json)

    async def iter_bytes(self, chunk_size: int = 1024):
        iterator = self._response.iter_bytes(chunk_size)
        while True:
            chunk = await asyncio.to_thread(_next_chunk, iterator)
            if chunk is None:
                break
            yield chunk

    async def aclose(self):
        await asyncio.to_thread(self._response.close)

    def raise_for_status(self):
        self._response.raise_for_status()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()
        return False


class AsyncSession:
    def __init__(self, session: Optional[Session] = None):
        self._session = session or Session()
        self.headers = self._session.headers

    async def request(self, method: str, url: str, **kwargs) -> AsyncResponse:
        response = await asyncio.to_thread(self._session.request, method, url, **kwargs)
        return AsyncResponse(response)

    async def get(self, url: str, **kwargs) -> AsyncResponse:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> AsyncResponse:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs) -> AsyncResponse:
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs) -> AsyncResponse:
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs) -> AsyncResponse:
        return await self.request("DELETE", url, **kwargs)

    async def head(self, url: str, **kwargs) -> AsyncResponse:
        return await self.request("HEAD", url, **kwargs)

    async def options(self, url: str, **kwargs) -> AsyncResponse:
        return await self.request("OPTIONS", url, **kwargs)

    async def close(self):
        await asyncio.to_thread(self._session.close)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
        return False


ClientSession = AsyncSession


async def arequest(method: str, url: str, **kwargs) -> AsyncResponse:
    async with AsyncSession() as session:
        return await session.request(method, url, **kwargs)


async def aget(url: str, **kwargs) -> AsyncResponse:
    return await arequest("GET", url, **kwargs)


async def apost(url: str, **kwargs) -> AsyncResponse:
    return await arequest("POST", url, **kwargs)


async def aput(url: str, **kwargs) -> AsyncResponse:
    return await arequest("PUT", url, **kwargs)


async def apatch(url: str, **kwargs) -> AsyncResponse:
    return await arequest("PATCH", url, **kwargs)


async def adelete(url: str, **kwargs) -> AsyncResponse:
    return await arequest("DELETE", url, **kwargs)


async def ahead(url: str, **kwargs) -> AsyncResponse:
    return await arequest("HEAD", url, **kwargs)


async def aoptions(url: str, **kwargs) -> AsyncResponse:
    return await arequest("OPTIONS", url, **kwargs)
