"""Small asynchronous client for the official Pi-hole v6 API."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from typing import Any

import aiohttp

from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USE_HTTPS,
    CONF_VERIFY_SSL,
    REQUEST_TIMEOUT,
)


def normalize_host(value: Any) -> str:
    """Validate and canonicalize a bare DNS hostname or IP address."""
    if not isinstance(value, str):
        raise ValueError("Pi-hole host must be a hostname or IP address")
    host = value.strip()
    if not host or any(character in host for character in "/?#@\\%") or "://" in host:
        raise ValueError("Pi-hole host must not include a URL, path, or credentials")
    bracketed = host.startswith("[") or host.endswith("]")
    if bracketed:
        if not (host.startswith("[") and host.endswith("]")):
            raise ValueError("Brackets are only valid around IPv6 addresses")
        try:
            address = ipaddress.ip_address(host[1:-1])
        except ValueError as err:
            raise ValueError("Brackets are only valid around IPv6 addresses") from err
        if not isinstance(address, ipaddress.IPv6Address):
            raise ValueError("Brackets are only valid around IPv6 addresses")
        return address.compressed
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        pass
    if ":" in host:
        raise ValueError("IPv6 hosts must be valid address literals")
    host = host[:-1] if host.endswith(".") else host
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as err:
        raise ValueError("Pi-hole host is not a valid hostname") from err
    if len(ascii_host) > 253 or not ascii_host:
        raise ValueError("Pi-hole host is not a valid hostname")
    labels = ascii_host.split(".")
    if any(
        not label
        or len(label) > 63
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        for label in labels
    ):
        raise ValueError("Pi-hole host is not a valid hostname")
    if re.fullmatch(r"[0-9.]+", ascii_host):
        raise ValueError("Pi-hole IPv4 address is not valid")
    return ascii_host


def endpoint_unique_id(host: str, port: int, use_https: bool) -> str:
    """Build the canonical discovery key for a host, protocol, and explicit port."""
    normalized_host = normalize_host(host)
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        address = None
    if isinstance(address, ipaddress.IPv6Address):
        normalized_host = f"[{normalized_host}]"
    scheme = "https" if use_https else "http"
    return f"{scheme}://{normalized_host}:{int(port)}"


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
        host = normalize_host(data[CONF_HOST])
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

        session = payload.get("session") if isinstance(payload, dict) else None
        if not isinstance(session, dict):
            raise PiHoleConnectionError("Respuesta de /api/auth no válida")
        if "valid" not in session or not isinstance(session["valid"], bool):
            raise PiHoleConnectionError("Respuesta de /api/auth no válida")
        if not session["valid"]:
            raise PiHoleAuthenticationError
        sid = session.get("sid")
        if (
            not isinstance(sid, str)
            or not sid.strip()
            or any(character.isspace() or not character.isprintable() for character in sid)
        ):
            raise PiHoleConnectionError("Respuesta de /api/auth no válida")
        self._sid = sid

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
