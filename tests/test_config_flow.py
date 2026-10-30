"""Config, reauth and options flow tests."""

from __future__ import annotations

from elli_client import InvalidOAuthCallback, InvalidOAuthState, TokenExchangeError
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.elli_charger.const import (
    CONF_CALLBACK_URL,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)

from .conftest import ACCOUNT_ID, make_tokens

CALLBACK = "com.elli.ios.emsp://login.elli.eco/ios/com.elli.ios.emsp/callback?code=c&state=state-1"


async def test_user_flow(hass: HomeAssistant, mock_client) -> None:
    """Login link shown, callback pasted, entry created with the refresh token only."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"]["login_url"].startswith("https://login.elli.eco/")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CALLBACK_URL: f"  {CALLBACK}\n"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garage"
    assert result["data"] == {CONF_REFRESH_TOKEN: "refresh-1"}
    assert result["result"].unique_id == ACCOUNT_ID
    assert mock_client.exchange_callback.call_args.args[0] == CALLBACK


async def test_user_flow_errors_get_a_new_link(hass: HomeAssistant, mock_client) -> None:
    """Every failed attempt shows an error and a fresh authorization link."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    for exc, error in (
        (InvalidOAuthState("x"), "state_mismatch"),
        (InvalidOAuthCallback("x"), "invalid_callback"),
        (TokenExchangeError("x"), "invalid_auth"),
        (ValueError("Failed to get stations: 503 - x"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ):
        before = mock_client.create_authorization.call_count
        mock_client.exchange_callback.side_effect = exc
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CALLBACK_URL: CALLBACK}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": error}
        assert mock_client.create_authorization.call_count == before + 1

    mock_client.exchange_callback.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CALLBACK_URL: CALLBACK}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_duplicate_account_aborts(hass: HomeAssistant, mock_client, config_entry) -> None:
    """The same Elli account cannot be added twice."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CALLBACK_URL: CALLBACK}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, mock_client, config_entry) -> None:
    """Reauth stores the new refresh token."""
    config_entry.add_to_hass(hass)
    mock_client.exchange_callback.return_value = make_tokens(refresh="refresh-new")
    # The reload after reauth refreshes with the new token; Elli keeps it.
    mock_client.refresh.return_value = make_tokens(refresh="refresh-new")
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CALLBACK_URL: CALLBACK}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data == {CONF_REFRESH_TOKEN: "refresh-new"}


async def test_reauth_wrong_account(hass: HomeAssistant, mock_client, config_entry) -> None:
    """Logging in with another account during reauth is refused."""
    config_entry.add_to_hass(hass)
    mock_client.exchange_callback.return_value = make_tokens(sub="auth0|someone-else")
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CALLBACK_URL: CALLBACK}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert config_entry.data == {CONF_REFRESH_TOKEN: "refresh-1"}


async def test_reauth_migrates_legacy_entry(hass: HomeAssistant, mock_client) -> None:
    """A 1.x email/password entry gets the token and the account ID as unique ID."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="me@example.com",
        data={"email": "me@example.com", "password": "secret"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CALLBACK_URL: CALLBACK}
    )
    assert result["reason"] == "reauth_successful"
    assert entry.data == {CONF_REFRESH_TOKEN: "refresh-1"}
    assert entry.unique_id == ACCOUNT_ID


async def test_options_change_interval_without_reload(
    hass: HomeAssistant, mock_client, config_entry
) -> None:
    """The polling interval is applied in place."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 10.0}
    )
    await hass.async_block_till_done()
    assert config_entry.options == {CONF_SCAN_INTERVAL: 10}
    assert config_entry.runtime_data.update_interval.total_seconds() == 600
