"""Config flow for Pi-hole query tracking."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .api import (
    PiHoleAuthenticationError,
    PiHoleClient,
    PiHoleConnectionError,
)
from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USE_HTTPS,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)


def _schema(defaults: dict[str, Any] | None = None, *, include_connection: bool = True) -> vol.Schema:
    defaults = defaults or {}
    fields: dict[Any, Any] = {
        vol.Required(
            CONF_SCAN_INTERVAL,
            default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
    }
    if include_connection:
        fields = {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(CONF_USE_HTTPS, default=defaults.get(CONF_USE_HTTPS, False)): bool,
            vol.Required(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            **fields,
        }
    return vol.Schema(fields)


async def _validate_connection(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Validate credentials and ensure the local v6 queries endpoint is available."""
    client = PiHoleClient(async_get_clientsession(hass), data)
    try:
        await client.async_authenticate()
        await client.async_get_queries(length=1)
    finally:
        await client.async_close()


class PiHoleQueriesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a Pi-hole v6 instance from Home Assistant's UI."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Configure host, API credentials, and polling interval."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip().rstrip("/")
            if not host:
                errors["base"] = "invalid_host"
            else:
                data = {**user_input, CONF_HOST: host}
                try:
                    await _validate_connection(self.hass, data)
                except PiHoleAuthenticationError:
                    errors["base"] = "invalid_auth"
                except PiHoleConnectionError:
                    errors["base"] = "cannot_connect"
                else:
                    scheme = "https" if data[CONF_USE_HTTPS] else "http"
                    unique = f"{scheme}://{host}:{data[CONF_PORT]}".lower()
                    await self.async_set_unique_id(unique)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title=f"Pi-hole · {host}", data=data)
        return self.async_show_form(step_id="user", data_schema=_schema(user_input), errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> dict[str, Any]:
        """Begin credential renewal after Pi-hole rejects a stored password."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Accept and validate a replacement application password."""
        entry = self._reauth_entry
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            try:
                await _validate_connection(self.hass, data)
            except PiHoleAuthenticationError:
                errors["base"] = "invalid_auth"
            except PiHoleConnectionError:
                errors["base"] = "cannot_connect"
            else:
                self.hass.config_entries.async_update_entry(entry, data=data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
            description_placeholders={"host": entry.data[CONF_HOST]},
        )

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "PiHoleQueriesOptionsFlow":
        """Return interval options flow."""
        return PiHoleQueriesOptionsFlow()


class PiHoleQueriesOptionsFlow(config_entries.OptionsFlow):
    """Allow changing query polling frequency."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Set the interval used for polling Pi-hole."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        defaults = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(defaults, include_connection=False))
