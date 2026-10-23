"""Config flow for Pi-hole query tracking."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from homeassistant.helpers import entity_registry as er

from .api import (
    PiHoleAuthenticationError,
    PiHoleCertificateError,
    PiHoleClient,
    PiHoleConnectionError,
    endpoint_unique_id,
    normalize_host,
)
from .const import (
    CONF_HOST,
    CONF_ACTIVITY_MODE,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_RECONFIGURE_SOURCE_ACTION,
    CONF_RETENTION_MODE,
    CONF_SOURCE_GENERATION,
    CONF_SOURCE_ID,
    CONF_SCAN_INTERVAL,
    CONF_USE_HTTPS,
    CONF_VERIFY_SSL,
    DEFAULT_PORT,
    DEFAULT_ACTIVITY_MODE,
    DEFAULT_RETENTION_MODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    ACTIVITY_ALL,
    ACTIVITY_BLOCKED,
    ACTIVITY_NONE,
    RETENTION_AGGREGATE,
    RETENTION_DETAIL,
    LEGACY_SOURCE_GENERATION,
    RECONFIGURE_NEW_SOURCE,
    RECONFIGURE_SAME_SOURCE,
)


def _schema(
    defaults: dict[str, Any] | None = None,
    *,
    include_connection: bool = True,
    include_retention: bool = False,
) -> vol.Schema:
    defaults = defaults or {}
    fields: dict[Any, Any] = {
        vol.Required(
            CONF_SCAN_INTERVAL,
            default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
    }
    if include_retention:
        fields.update(
            {
                vol.Required(
                    CONF_RETENTION_MODE,
                    default=defaults.get(CONF_RETENTION_MODE, DEFAULT_RETENTION_MODE),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[RETENTION_DETAIL, RETENTION_AGGREGATE],
                        translation_key="retention_mode",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_ACTIVITY_MODE,
                    default=defaults.get(CONF_ACTIVITY_MODE, DEFAULT_ACTIVITY_MODE),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[ACTIVITY_ALL, ACTIVITY_BLOCKED, ACTIVITY_NONE],
                        translation_key="activity_mode",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
    if include_connection:
        fields = {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(CONF_USE_HTTPS, default=defaults.get(CONF_USE_HTTPS, False)): bool,
            vol.Required(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, True)): bool,
            vol.Required(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            **fields,
        }
    return vol.Schema(fields)


def _reconfigure_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Ask for an endpoint and require an explicit source-identity decision."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(CONF_USE_HTTPS, default=defaults.get(CONF_USE_HTTPS, False)): bool,
            vol.Required(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, True)): bool,
            vol.Optional(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_RECONFIGURE_SOURCE_ACTION): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[RECONFIGURE_SAME_SOURCE, RECONFIGURE_NEW_SOURCE],
                    translation_key="source_action",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


def _entry_endpoint_unique_id(data: dict[str, Any]) -> str | None:
    """Return a canonical endpoint key for an existing entry, if valid."""
    try:
        return endpoint_unique_id(data[CONF_HOST], data[CONF_PORT], data[CONF_USE_HTTPS])
    except (KeyError, TypeError, ValueError):
        return None


def _endpoint_is_configured(
    hass: HomeAssistant, endpoint: str, *, exclude_entry_id: str | None = None
) -> bool:
    """Check actual stored endpoints, independent of potentially historical unique IDs."""
    return any(
        entry.entry_id != exclude_entry_id
        and _entry_endpoint_unique_id(entry.data) == endpoint
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


def _archive_client_entities(hass: HomeAssistant, entry: config_entries.ConfigEntry) -> None:
    """Disable previous-generation client sensors while retaining their Recorder history."""
    generation = entry.data.get(CONF_SOURCE_GENERATION, LEGACY_SOURCE_GENERATION)
    prefix = (
        entry.unique_id or entry.entry_id
        if generation == LEGACY_SOURCE_GENERATION
        else f"{entry.data[CONF_SOURCE_ID]}_{generation}"
    ) + "_"
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity.platform == DOMAIN
            and entity.domain == "sensor"
            and entity.unique_id.startswith(prefix)
        ):
            registry.async_update_entity(
                entity.entity_id,
                disabled_by=RegistryEntryDisabler.INTEGRATION,
            )


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

    VERSION = 8

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Configure host, API credentials, and polling interval."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                host = normalize_host(user_input[CONF_HOST])
            except ValueError:
                errors["base"] = "invalid_host"
            else:
                data = {**user_input, CONF_HOST: host}
                unique = endpoint_unique_id(host, data[CONF_PORT], data[CONF_USE_HTTPS])
                if _endpoint_is_configured(self.hass, unique):
                    errors["base"] = "already_configured"
                else:
                    try:
                        await _validate_connection(self.hass, data)
                    except PiHoleAuthenticationError:
                        errors["base"] = "invalid_auth"
                    except PiHoleCertificateError:
                        errors["base"] = "ssl_certificate"
                    except PiHoleConnectionError:
                        errors["base"] = "cannot_connect"
                    else:
                        # Connection validation yields; another flow may have
                        # claimed this endpoint while the request was in flight.
                        if _endpoint_is_configured(self.hass, unique):
                            errors["base"] = "already_configured"
                        else:
                            source_id = str(uuid4())
                            data[CONF_SOURCE_ID] = source_id
                            data[CONF_SOURCE_GENERATION] = LEGACY_SOURCE_GENERATION
                            existing = await self.async_set_unique_id(unique)
                            if (
                                existing is not None
                                and _entry_endpoint_unique_id(existing.data) != unique
                            ):
                                # Reconfigure preserves the old unique_id for legacy
                                # entity IDs. If this endpoint was released, key the
                                # new entry by its source so that stale ownership does
                                # not block adding it again.
                                await self.async_set_unique_id(f"source:{source_id}")
                            self._abort_if_unique_id_configured()
                            return self.async_create_entry(title=f"Pi-hole · {host}", data=data)
        return self.async_show_form(step_id="user", data_schema=_schema(user_input), errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Change endpoint after validating it and an explicit source decision."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="reconfigure_entry_not_found")
        original_data = dict(entry.data)
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                host = normalize_host(user_input[CONF_HOST])
            except ValueError:
                errors["base"] = "invalid_host"
            else:
                endpoint = endpoint_unique_id(
                    host, user_input[CONF_PORT], user_input[CONF_USE_HTTPS]
                )
                if _endpoint_is_configured(
                    self.hass, endpoint, exclude_entry_id=entry.entry_id
                ):
                    errors["base"] = "already_configured"
                else:
                    password = user_input.get(CONF_PASSWORD)
                    data = {
                        **original_data,
                        CONF_HOST: host,
                        CONF_PORT: user_input[CONF_PORT],
                        CONF_USE_HTTPS: user_input[CONF_USE_HTTPS],
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    }
                    if isinstance(password, str) and password:
                        data[CONF_PASSWORD] = password
                    try:
                        await _validate_connection(self.hass, data)
                    except PiHoleAuthenticationError:
                        errors["base"] = "invalid_auth"
                    except PiHoleCertificateError:
                        errors["base"] = "ssl_certificate"
                    except PiHoleConnectionError:
                        errors["base"] = "cannot_connect"
                    else:
                        current_entry = self.hass.config_entries.async_get_entry(entry.entry_id)
                        if current_entry is None:
                            return self.async_abort(reason="reconfigure_entry_not_found")
                        if current_entry.data != original_data:
                            return self.async_abort(reason="reconfigure_entry_changed")
                        # Another entry may have claimed this endpoint while
                        # validation was in flight.
                        if _endpoint_is_configured(
                            self.hass, endpoint, exclude_entry_id=current_entry.entry_id
                        ):
                            errors["base"] = "already_configured"
                        else:
                            action = user_input[CONF_RECONFIGURE_SOURCE_ACTION]
                            if action == RECONFIGURE_NEW_SOURCE:
                                data[CONF_SOURCE_GENERATION] = str(uuid4())
                                _archive_client_entities(self.hass, current_entry)
                            # Preserve the entry unique_id. In the first generation it
                            # is part of the registered entity IDs; endpoint equality
                            # is checked against the actual data of every other entry.
                            return self.async_update_reload_and_abort(
                                current_entry,
                                data=data,
                                title=f"Pi-hole · {host}",
                                reason="reconfigure_successful",
                            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_reconfigure_schema(entry.data),
            errors=errors,
            description_placeholders={"host": entry.data.get(CONF_HOST, "")},
        )

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
            except PiHoleCertificateError:
                errors["base"] = "ssl_certificate"
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
    """Allow changing polling frequency and query retention."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Set polling frequency, retention mode, and Activity policy."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        defaults = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(
                defaults,
                include_connection=False,
                include_retention=True,
            ),
        )
