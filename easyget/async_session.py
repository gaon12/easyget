import asyncio
from typing import Any, Awaitable, Callable, List, Optional

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
        self._aclose_callbacks: List[Callable[[], Awaitable[None]]] = []

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

    @property
    def closed(self) -> bool:
        return self._response.closed

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

    def raise_for_status(self):
        self._response.raise_for_status()

    def add_aclose_callback(self, callback: Callable[[], Awaitable[None]]):
        self._aclose_callbacks.append(callback)

    async def aclose(self):
        await asyncio.to_thread(self._response.close)
        callbacks = self._aclose_callbacks[:]
        self._aclose_callbacks.clear()
        for callback in callbacks:
            try:
                await callback()
            except Exception:
                pass

    async def release(self):
        await self.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()
        return False


class AsyncRequestContextManager:
    def __init__(self, coro: Awaitable[AsyncResponse]):
        self._coro = coro
        self._response: Optional[AsyncResponse] = None

    def __await__(self):
        return self._coro.__await__()

    async def __aenter__(self) -> AsyncResponse:
        if self._response is None:
            self._response = await self._coro
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        if self._response is not None:
            await self._response.aclose()
        return False


class AsyncSession:
    def __init__(
        self,
        session: Optional[Session] = None,
        max_concurrency: Optional[int] = None,
    ):
        if max_concurrency is not None and max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._session = session or Session()
        self._semaphore = asyncio.Semaphore(max_concurrency) if max_concurrency else None
        self.headers = self._session.headers
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def _run_request(self, method: str, url: str, kwargs: dict) -> Response:
        if self._closed:
            raise RuntimeError("AsyncSession is closed")
        if self._semaphore is not None:
            async with self._semaphore:
                return await asyncio.to_thread(self._session.request, method, url, **kwargs)
        return await asyncio.to_thread(self._session.request, method, url, **kwargs)

    async def _request(self, method: str, url: str, **kwargs) -> AsyncResponse:
        request_timeout = kwargs.pop("request_timeout", None)
        if request_timeout is None:
            response = await self._run_request(method, url, kwargs)
        else:
            response = await asyncio.wait_for(
                self._run_request(method, url, kwargs),
                timeout=float(request_timeout),
            )
        return AsyncResponse(response)

    def request(self, method: str, url: str, **kwargs) -> AsyncRequestContextManager:
        return AsyncRequestContextManager(self._request(method, url, **kwargs))

    def get(self, url: str, **kwargs) -> AsyncRequestContextManager:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> AsyncRequestContextManager:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs) -> AsyncRequestContextManager:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs) -> AsyncRequestContextManager:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs) -> AsyncRequestContextManager:
        return self.request("DELETE", url, **kwargs)

    def head(self, url: str, **kwargs) -> AsyncRequestContextManager:
        return self.request("HEAD", url, **kwargs)

    def options(self, url: str, **kwargs) -> AsyncRequestContextManager:
        return self.request("OPTIONS", url, **kwargs)

    async def close(self):
        if self._closed:
            return
        self._closed = True
        await asyncio.to_thread(self._session.close)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
        return False


ClientSession = AsyncSession


async def arequest(method: str, url: str, **kwargs) -> AsyncResponse:
    stream = bool(kwargs.get("stream"))
    session = AsyncSession()
    try:
        response = await session.request(method, url, **kwargs)
    except Exception:
        await session.close()
        raise

    if stream:
        response.add_aclose_callback(session.close)
    else:
        await session.close()
    return response


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
