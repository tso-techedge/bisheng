"""Lightweight wrapper around requests library, with async support."""
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, Optional, Tuple, Union

import aiohttp
import requests
from loguru import logger
from pydantic import ConfigDict, BaseModel


class Requests(BaseModel):
    """Wrapper around requests to handle auth and async.

    The main purpose of this wrapper is to handle authentication (by saving
    headers) and enable easy async methods on the same base object.
    """

    headers: Optional[Dict[str, str]] = None
    aiosession: Optional[aiohttp.ClientSession] = None
    auth: Optional[Any] = None
    request_timeout: Union[float, Tuple[float, float]] = 120
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        """GET the URL and return the text."""
        return requests.get(url,
                            headers=self.headers,
                            auth=self.auth,
                            timeout=self.request_timeout,
                            **kwargs)

    def post(self, url: str, json: Dict[str, Any], **kwargs: Any) -> requests.Response:
        """POST to the URL and return the text."""
        return requests.post(url,
                             json=json,
                             headers=self.headers,
                             auth=self.auth,
                             timeout=self.request_timeout,
                             **kwargs)

    def patch(self, url: str, json: Dict[str, Any], **kwargs: Any) -> requests.Response:
        """PATCH the URL and return the text."""
        return requests.patch(url,
                              json=json,
                              headers=self.headers,
                              auth=self.auth,
                              timeout=self.request_timeout,
                              **kwargs)

    def put(self, url: str, json: Dict[str, Any], **kwargs: Any) -> requests.Response:
        """PUT the URL and return the text."""
        return requests.put(url,
                            json=json,
                            headers=self.headers,
                            auth=self.auth,
                            timeout=self.request_timeout,
                            **kwargs)

    def delete(self, url: str, **kwargs: Any) -> requests.Response:
        """DELETE the URL and return the text."""
        return requests.delete(url,
                               headers=self.headers,
                               auth=self.auth,
                               timeout=self.request_timeout,
                               **kwargs)

    @asynccontextmanager
    async def _arequest(self, method: str, url: str,
                        **kwargs: Any) -> AsyncGenerator[aiohttp.ClientResponse, None]:
        """Make an async request."""
        if not self.aiosession:
            if not self.request_timeout:
                self.request_timeout = 120
            if isinstance(self.request_timeout, tuple):
                timeout = aiohttp.ClientTimeout(connect=self.request_timeout[0],
                                                total=self.request_timeout[1])
            else:
                timeout = aiohttp.ClientTimeout(total=self.request_timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                logger.info(f'aio_http url={url}')
                async with session.request(method, url, headers=self.headers,
                                           **kwargs) as response:
                    yield response
        else:
            async with self.aiosession:
                async with self.aiosession.request(method,
                                                   url,
                                                   headers=self.headers,
                                                   auth=self.auth,
                                                   **kwargs) as response:
                    yield response

    @asynccontextmanager
    async def aget(self, url: str, **kwargs: Any) -> AsyncGenerator[aiohttp.ClientResponse, None]:
        """GET the URL and return the text asynchronously."""
        async with self._arequest('GET', url, auth=self.auth, **kwargs) as response:
            yield response

    @asynccontextmanager
    async def apost(self, url: str, json: Dict[str, Any],
                    **kwargs: Any) -> AsyncGenerator[aiohttp.ClientResponse, None]:
        """POST to the URL and return the text asynchronously."""
        async with self._arequest('POST', url, json=json, auth=self.auth, **kwargs) as response:
            yield response

    @asynccontextmanager
    async def apatch(self, url: str, json: Dict[str, Any],
                     **kwargs: Any) -> AsyncGenerator[aiohttp.ClientResponse, None]:
        """PATCH the URL and return the text asynchronously."""
        async with self._arequest('PATCH', url, json=json, auth=self.auth, **kwargs) as response:
            yield response

    @asynccontextmanager
    async def aput(self, url: str, json: Dict[str, Any],
                   **kwargs: Any) -> AsyncGenerator[aiohttp.ClientResponse, None]:
        """PUT the URL and return the text asynchronously."""
        async with self._arequest('PUT', url, json=json, auth=self.auth, **kwargs) as response:
            yield response

    @asynccontextmanager
    async def adelete(self, url: str,
                      **kwargs: Any) -> AsyncGenerator[aiohttp.ClientResponse, None]:
        """DELETE the URL and return the text asynchronously."""
        async with self._arequest('DELETE', url, auth=self.auth, **kwargs) as response:
            yield response


class TextRequestsWrapper(BaseModel):
    """Lightweight wrapper around requests library.

    The main purpose of this wrapper is to always return a text output.
    """

    headers: Optional[Dict[str, str]] = None
    aiosession: Optional[aiohttp.ClientSession] = None
    auth: Optional[Any] = None
    request_timeout: Union[float, Tuple[float, float]] = 120
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    @property
    def requests(self) -> Requests:
        return Requests(headers=self.headers,
                        aiosession=self.aiosession,
                        auth=self.auth,
                        request_timeout=self.request_timeout)

    def get(self, url: str, **kwargs: Any) -> str:
        """GET the URL and return the text."""
        return self.requests.get(url, **kwargs).text

    def post(self, url: str, json: Dict[str, Any], **kwargs: Any) -> str:
        """POST to the URL and return the text."""
        return self.requests.post(url, json, **kwargs).text

    def patch(self, url: str, json: Dict[str, Any], **kwargs: Any) -> str:
        """PATCH the URL and return the text."""
        return self.requests.patch(url, json, **kwargs).text

    def put(self, url: str, json: Dict[str, Any], **kwargs: Any) -> str:
        """PUT the URL and return the text."""
        return self.requests.put(url, json, **kwargs).text

    def delete(self, url: str, **kwargs: Any) -> str:
        """DELETE the URL and return the text."""
        return self.requests.delete(url, **kwargs).text

    async def aget(self, url: str, **kwargs: Any) -> str:
        """GET the URL and return the text asynchronously."""
        async with self.requests.aget(url, **kwargs) as response:
            return await response.text()

    async def apost(self, url: str, json: Dict[str, Any], **kwargs: Any) -> str:
        """POST to the URL and return the text asynchronously."""
        async with self.requests.apost(url, json, **kwargs) as response:
            return await response.text()

    async def apatch(self, url: str, json: Dict[str, Any], **kwargs: Any) -> str:
        """PATCH the URL and return the text asynchronously."""
        async with self.requests.apatch(url, json, **kwargs) as response:
            return await response.text()

    async def aput(self, url: str, json: Dict[str, Any], **kwargs: Any) -> str:
        """PUT the URL and return the text asynchronously."""
        async with self.requests.aput(url, json, **kwargs) as response:
            return await response.text()

    async def adelete(self, url: str, **kwargs: Any) -> str:
        """DELETE the URL and return the text asynchronously."""
        async with self.requests.adelete(url, **kwargs) as response:
            return await response.text()


# For backwards compatibility
RequestsWrapper = TextRequestsWrapper
