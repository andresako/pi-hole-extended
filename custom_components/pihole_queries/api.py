"""Small asynchronous client for the official Pi-hole v6 API."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USE_HTTPS,
    CONF_VERIFY_SSL,
    REQUEST_TIMEOUT,
)


class PiHoleApiError(Exception):
    """Base API error, intentionally excluding request credentials and bodies."""


class PiHoleAuthenticationError(PiHoleApiError):
    """The application password was rejected or the session expired."""


class PiHoleConnectionError(PiHoleApiError):
    """The Pi-hole could not be reached or returned an invalid response."""


class PiHoleCertificateError(PiHoleConnectionError):
    """The HTTPS certificate could not be verified."""


class PiHoleClient:
    """API client with session renewal after an expired Pi-hole SID."""

    def __init__(self, session: aiohttp.ClientSession, data: dict[str, Any]) -> None:
        host = str(data[CONF_HOST]).strip().rstrip("/")
        # Accept a bare hostname/IP in the config flow. Bracket IPv6 literals in URLs.
        if "://" in host:
            parsed = urlsplit(host)
            host = parsed.hostname or host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        self._base_url = f"{'https' if data[CONF_USE_HTTPS] else 'http'}://{host}:{data[CONF_PORT]}"
        self._password = str(data[CONF_PASSWORD])
        self._verify_ssl = bool(data.get(CONF_VERIFY_SSL, True))
        self._session = session
        self._sid: str | None = None
        self._lock = asyncio.Lock()

    async def async_authenticate(self) -> None:
        """Obtain a short-lived SID using the configured application password."""
        try:
            async with self._session.post(
                f"{self._base_url}/api/auth",
                json={"password": self._password},
                ssl=self._verify_ssl,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                if response.status == 401:
                    raise PiHoleAuthenticationError
                if response.status >= 400:
                    raise PiHoleConnectionError(
                        f"/api/auth respondió HTTP {response.status}"
                    )
                payload = await response.json(content_type=None)
        except PiHoleAuthenticationError:
            raise
        except PiHoleConnectionError:
            raise
        except aiohttp.ClientConnectorCertificateError as err:
            raise PiHoleCertificateError(
                "No se pudo verificar el certificado HTTPS de Pi-hole"
            ) from err
        except asyncio.TimeoutError as err:
            raise PiHoleConnectionError("tiempo de espera agotado al llamar a /api/auth") from err
        except aiohttp.ClientError as err:
            raise PiHoleConnectionError(
                f"fallo de red al llamar a /api/auth ({type(err).__name__})"
            ) from err
        except ValueError as err:
            raise PiHoleConnectionError("Pi-hole devolvió JSON no válido en /api/auth") from err

        session = payload.get("session", {}) if isinstance(payload, dict) else {}
        if not session.get("valid") or not session.get("sid"):
            raise PiHoleAuthenticationError
        self._sid = str(session["sid"])

    async def async_get_queries(self, *, length: int, cursor: int | None = None) -> dict[str, Any]:
        """Fetch a query page, retrying exactly once after an expired SID."""
        params: dict[str, int] = {"length": length}
        if cursor is not None:
            params["cursor"] = cursor
        for attempt in range(2):
            if self._sid is None:
                await self.async_authenticate()
            try:
                async with self._session.get(
                    f"{self._base_url}/api/queries",
                    params=params,
                    headers={"X-FTL-SID": self._sid or ""},
                    ssl=self._verify_ssl,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as response:
                    if response.status == 401:
                        self._sid = None
                        if attempt == 0:
                            continue
                        raise PiHoleAuthenticationError
                    if response.status >= 400:
                        raise PiHoleConnectionError(
                            f"/api/queries respondió HTTP {response.status}"
                        )
                    payload = await response.json(content_type=None)
            except PiHoleAuthenticationError:
                raise
            except PiHoleConnectionError:
                raise
            except aiohttp.ClientConnectorCertificateError as err:
                raise PiHoleCertificateError(
                    "No se pudo verificar el certificado HTTPS de Pi-hole"
                ) from err
            except asyncio.TimeoutError as err:
                raise PiHoleConnectionError(
                    "tiempo de espera agotado al llamar a /api/queries"
                ) from err
            except aiohttp.ClientError as err:
                raise PiHoleConnectionError(
                    f"fallo de red al llamar a /api/queries ({type(err).__name__})"
                ) from err
            except ValueError as err:
                raise PiHoleConnectionError(
                    "Pi-hole devolvió JSON no válido en /api/queries"
                ) from err
            if not isinstance(payload, dict) or not isinstance(payload.get("queries"), list):
                raise PiHoleConnectionError("Respuesta de /api/queries no válida")
            return payload
        raise PiHoleAuthenticationError

    async def async_close(self) -> None:
        """Invalidate the active SID when unloading the config entry."""
        sid, self._sid = self._sid, None
        if sid is None:
            return
        try:
            async with self._session.delete(
                f"{self._base_url}/api/auth",
                headers={"X-FTL-SID": sid},
                ssl=self._verify_ssl,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ):
                pass
        except (aiohttp.ClientError, asyncio.TimeoutError):
            # Logout is best effort; Pi-hole expires idle sessions on its own.
            return
