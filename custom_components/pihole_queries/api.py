"""Small asynchronous client for the official Pi-hole v6 API."""

from __future__ import annotations

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
        not label or len(label) > 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
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
                    raise PiHoleConnectionError(f"/api/auth returned HTTP {response.status}")
                payload = await response.json(content_type=None)
        except PiHoleAuthenticationError:
            raise
        except PiHoleConnectionError:
            raise
        except aiohttp.ClientConnectorCertificateError as err:
            raise PiHoleCertificateError("Could not verify the Pi-hole HTTPS certificate") from err
        except TimeoutError as err:
            raise PiHoleConnectionError("timed out calling /api/auth") from err
        except aiohttp.ClientError as err:
            raise PiHoleConnectionError(
                f"network failure calling /api/auth ({type(err).__name__})"
            ) from err
        except ValueError as err:
            raise PiHoleConnectionError("Pi-hole returned invalid JSON from /api/auth") from err

        session = payload.get("session") if isinstance(payload, dict) else None
        if not isinstance(session, dict):
            raise PiHoleConnectionError("Invalid response from /api/auth")
        if "valid" not in session or not isinstance(session["valid"], bool):
            raise PiHoleConnectionError("Invalid response from /api/auth")
        if not session["valid"]:
            raise PiHoleAuthenticationError
        sid = session.get("sid")
        if (
            not isinstance(sid, str)
            or not sid.strip()
            or any(character.isspace() or not character.isprintable() for character in sid)
        ):
            raise PiHoleConnectionError("Invalid response from /api/auth")
        self._sid = sid

    async def _async_get(self, path: str, params: dict[str, int] | None = None) -> Any:
        """GET an API path as JSON, retrying exactly once after an expired SID."""
        for attempt in range(2):
            if self._sid is None:
                await self.async_authenticate()
            try:
                async with self._session.get(
                    f"{self._base_url}{path}",
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
                        raise PiHoleConnectionError(f"{path} returned HTTP {response.status}")
                    return await response.json(content_type=None)
            except PiHoleAuthenticationError:
                raise
            except PiHoleConnectionError:
                raise
            except aiohttp.ClientConnectorCertificateError as err:
                raise PiHoleCertificateError(
                    "Could not verify the Pi-hole HTTPS certificate"
                ) from err
            except TimeoutError as err:
                raise PiHoleConnectionError(f"timed out calling {path}") from err
            except aiohttp.ClientError as err:
                raise PiHoleConnectionError(
                    f"network failure calling {path} ({type(err).__name__})"
                ) from err
            except ValueError as err:
                raise PiHoleConnectionError(f"Pi-hole returned invalid JSON from {path}") from err
        raise PiHoleAuthenticationError

    async def async_get_queries(self, *, length: int, cursor: int | None = None) -> dict[str, Any]:
        """Fetch a query page."""
        params: dict[str, int] = {"length": length}
        if cursor is not None:
            params["cursor"] = cursor
        payload = await self._async_get("/api/queries", params)
        if not isinstance(payload, dict) or not isinstance(payload.get("queries"), list):
            raise PiHoleConnectionError("Invalid response from /api/queries")
        return payload

    async def async_get_local_hosts(self) -> dict[str, str]:
        """Map each IP with a Pi-hole local DNS record to its first hostname."""
        path = "/api/config/dns/hosts"
        payload = await self._async_get(path)
        try:
            records = payload["config"]["dns"]["hosts"]
        except (KeyError, TypeError) as err:
            raise PiHoleConnectionError(f"Invalid response from {path}") from err
        if not isinstance(records, list):
            raise PiHoleConnectionError(f"Invalid response from {path}")
        hosts: dict[str, str] = {}
        for record in records:
            fields = record.split() if isinstance(record, str) else []
            if len(fields) < 2:
                continue
            try:
                hosts.setdefault(ipaddress.ip_address(fields[0]).compressed, fields[1])
            except ValueError:
                continue
        return hosts

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
        except (TimeoutError, aiohttp.ClientError):
            # Logout is best effort; Pi-hole expires idle sessions on its own.
            return
