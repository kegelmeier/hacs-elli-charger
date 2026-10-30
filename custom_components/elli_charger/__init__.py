"""The Elli Charger integration."""

from __future__ import annotations

from datetime import timedelta

from elli_client import ElliAPIClient, ElliClientError, ReauthenticationRequired
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import CONF_REFRESH_TOKEN, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES
from .coordinator import ElliConfigEntry, ElliDataUpdateCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ElliConfigEntry) -> bool:
    """Set up Elli Charger from a config entry."""
    if CONF_REFRESH_TOKEN not in entry.data:
        # Entries from 1.x stored email/password, which Elli's login no
        # longer accepts (Cloudflare Turnstile). Ask for the browser login.
        raise ConfigEntryAuthFailed("This entry needs the new browser login")

    # ElliAPIClient() builds an httpx client, which loads the CA bundle
    # synchronously, so it must not be created on the event loop.
    client = await hass.async_add_executor_job(ElliAPIClient)
    coordinator = ElliDataUpdateCoordinator(hass, entry, client)
    try:
        await coordinator.async_refresh_tokens()
    except ReauthenticationRequired as err:
        await hass.async_add_executor_job(client.close)
        raise ConfigEntryAuthFailed(str(err)) from err
    except ElliClientError as err:
        await hass.async_add_executor_job(client.close)
        raise ConfigEntryNotReady(f"Could not reach the Elli login: {err}") from err

    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ElliConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await hass.async_add_executor_job(entry.runtime_data.client.close)
    return unload_ok


async def _async_options_updated(hass: HomeAssistant, entry: ElliConfigEntry) -> None:
    """Apply a changed polling interval.

    The coordinator also updates the entry whenever Elli rotates the refresh
    token, so this must not reload the entry.
    """
    entry.runtime_data.update_interval = timedelta(
        minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
    )
