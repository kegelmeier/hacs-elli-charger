"""Config flow for the Elli Charger integration.

Elli's login page has a Cloudflare Turnstile check, so the integration cannot
log in with email and password. Instead the user logs in in their own browser
(OAuth2 authorization code with PKCE). Elli then redirects to the Elli app's
URL scheme (``com.elli.ios.emsp://...``), which a desktop browser cannot open;
the user copies that URL from the browser and pastes it here. Only the
resulting refresh token is stored.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from elli_client import (
    AuthorizationSession,
    ElliAPIClient,
    ElliClientError,
    InvalidOAuthCallback,
    InvalidOAuthState,
)
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CALLBACK_URL,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
)
from .coordinator import ElliConfigEntry

_LOGGER = logging.getLogger(__name__)

CALLBACK_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CALLBACK_URL): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
        )
    }
)


def _account_id(id_token: str | None) -> str | None:
    """Return the ``sub`` claim of an ID token without verifying it.

    The token comes straight from Elli's token endpoint over TLS; it is only
    used to tell accounts apart, not to authorize anything.
    """
    if not id_token:
        return None
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return None
    sub = claims.get("sub")
    return sub if isinstance(sub, str) and sub else None


def _login(
    callback_url: str, authorization: AuthorizationSession
) -> tuple[str, str | None, list[str]]:
    """Exchange the callback and check the API answers; runs in the executor."""
    client = ElliAPIClient()
    try:
        tokens = client.exchange_callback(callback_url.strip(), authorization)
        if not tokens.refresh_token:
            raise ElliClientError("Elli returned no refresh token")
        client.set_tokens(tokens)
        stations = client.get_stations()
        return (
            tokens.refresh_token,
            _account_id(tokens.id_token),
            [s.name for s in stations],
        )
    finally:
        client.close()


def _new_authorization() -> AuthorizationSession:
    client = ElliAPIClient()
    try:
        return client.create_authorization()
    finally:
        client.close()


class ElliConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the browser login for Elli Charger."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._authorization: AuthorizationSession | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ElliConfigEntry) -> ElliOptionsFlow:
        """Return the options flow."""
        return ElliOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the login link and take the pasted callback URL."""
        return await self._async_step_login("user", user_input)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start a new browser login for an existing entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the login link again for reauthentication."""
        return await self._async_step_login("reauth_confirm", user_input)

    async def _async_step_login(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and self._authorization is not None:
            try:
                (
                    refresh_token,
                    account_id,
                    station_names,
                ) = await self.hass.async_add_executor_job(
                    _login, user_input[CONF_CALLBACK_URL], self._authorization
                )
            except InvalidOAuthState:
                errors["base"] = "state_mismatch"
            except InvalidOAuthCallback:
                errors["base"] = "invalid_callback"
            except ElliClientError:
                errors["base"] = "invalid_auth"
            except ValueError as err:
                _LOGGER.debug("Elli API check after login failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during the Elli login")
                errors["base"] = "unknown"
            else:
                return await self._async_finish(refresh_token, account_id, station_names)
            # An authorization code works once; start over with a fresh link.
            self._authorization = None

        if self._authorization is None:
            self._authorization = await self.hass.async_add_executor_job(_new_authorization)

        return self.async_show_form(
            step_id=step_id,
            data_schema=CALLBACK_SCHEMA,
            errors=errors,
            description_placeholders={"login_url": self._authorization.authorization_url},
        )

    async def _async_finish(
        self, refresh_token: str, account_id: str | None, station_names: list[str]
    ) -> ConfigFlowResult:
        data = {CONF_REFRESH_TOKEN: refresh_token}
        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            # Entries from 1.x used the email as unique ID; adopt the account ID.
            if account_id and CONF_REFRESH_TOKEN in entry.data:
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(
                entry, unique_id=account_id or entry.unique_id, data=data
            )

        if account_id:
            await self.async_set_unique_id(account_id)
            self._abort_if_unique_id_configured()
        title = station_names[0] if len(station_names) == 1 else DEFAULT_NAME
        return self.async_create_entry(title=title, data=data)


class ElliOptionsFlow(OptionsFlow):
    """Polling interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL_MINUTES,
                            max=MAX_SCAN_INTERVAL_MINUTES,
                            step=1,
                            unit_of_measurement="min",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )
