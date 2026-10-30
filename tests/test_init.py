"""Setup, polling and sensor tests."""

from __future__ import annotations

from elli_client import ReauthenticationRequired, TokenRefreshError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elli_charger.const import CONF_REFRESH_TOKEN, DOMAIN

from .conftest import make_record, make_session, make_station, make_tokens

TOTAL = "sensor.garage_total_energy"
POWER = "sensor.garage_charging_power"
STATUS = "sensor.garage_status"
SESSION = "sensor.garage_session_energy"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_setup_idle(hass, mock_client, config_entry) -> None:
    """Without an active session the box is idle and the total is the record sum."""
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(STATUS).state == "idle"
    assert hass.states.get(POWER).state == "0"
    assert float(hass.states.get(TOTAL).state) == 15.0
    # Falls back to the last completed record.
    assert float(hass.states.get(SESSION).state) == 5.0


async def test_device_and_unique_ids(hass, mock_client, config_entry) -> None:
    """1.x unique IDs are kept and the firmware ends up on the device."""
    await _setup(hass, config_entry)
    entity = er.async_get(hass).async_get(STATUS)
    assert entity.unique_id == f"{config_entry.entry_id}_wallbox_st-1"
    device = dr.async_get(hass).async_get(entity.device_id)
    assert device.sw_version == "1.2.3"
    assert device.model == "Elli Charger Pro"


async def test_charging_adds_unbilled_session(hass, mock_client, config_entry) -> None:
    """A running session counts toward the total until its record exists."""
    await _setup(hass, config_entry)
    mock_client.get_charging_sessions.return_value = [make_session()]
    await _poll(hass, config_entry)
    assert hass.states.get(STATUS).state == "charging"
    assert hass.states.get(POWER).state == "11000"
    assert float(hass.states.get(TOTAL).state) == 17.5

    # Session ended, record not written yet: the total must not drop.
    mock_client.get_charging_sessions.return_value = []
    await _poll(hass, config_entry)
    assert float(hass.states.get(TOTAL).state) == 17.5

    # Record appears with the session's final energy: no double counting.
    mock_client.get_charging_records.return_value = [
        make_record("r3", 3000, 30, session_id="sess-active")
    ]
    await _poll(hass, config_entry)
    assert float(hass.states.get(TOTAL).state) == 18.0


async def test_incremental_records_request(hass, mock_client, config_entry) -> None:
    """After the first full read, only recent records are requested."""
    await _setup(hass, config_entry)
    first = mock_client.get_charging_records.call_args_list[0]
    assert first.kwargs["created_after"] is None
    mock_client.get_charging_records.return_value = []
    await _poll(hass, config_entry)
    later = mock_client.get_charging_records.call_args_list[-1]
    # Newest record created 2026-10-02T21:00Z, minus the 2 day overlap.
    assert later.kwargs["created_after"] == "2026-09-30T21:00:00Z"
    # Records already known are kept.
    assert float(hass.states.get(TOTAL).state) == 15.0


async def test_records_pagination(hass, mock_client, config_entry) -> None:
    """Full pages are followed until a short page arrives."""
    page1 = [make_record(f"a{i}", 100, 1 + i % 28) for i in range(100)]
    page2 = [make_record("b1", 1000, 3)]
    mock_client.get_charging_records.side_effect = [page1, page2]
    await _setup(hass, config_entry)
    assert float(hass.states.get(TOTAL).state) == 11.0
    offsets = [c.kwargs["offset"] for c in mock_client.get_charging_records.call_args_list]
    assert offsets == [0, 100]


async def test_sessions_404_is_not_fatal(hass, mock_client, config_entry) -> None:
    """Elli answers 404 RESOURCE_NOT_FOUND for some accounts."""
    mock_client.get_charging_sessions.side_effect = ValueError(
        'Failed to get charging sessions: 404 - {"errors":[{"code":"RESOURCE_NOT_FOUND"}]}'
    )
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(STATUS).state == "idle"


async def test_records_failure_keeps_total(hass, mock_client, config_entry) -> None:
    """A failed records read keeps the last total instead of dropping it."""
    await _setup(hass, config_entry)
    mock_client.get_charging_records.side_effect = ValueError(
        "Failed to get charging records: 500 - oops"
    )
    await _poll(hass, config_entry)
    assert float(hass.states.get(TOTAL).state) == 15.0


async def test_records_failure_on_first_read(hass, mock_client, config_entry) -> None:
    """Without a complete first read the total stays unknown, never partial."""
    mock_client.get_charging_records.side_effect = ValueError(
        "Failed to get charging records: 500 - oops"
    )
    await _setup(hass, config_entry)
    assert hass.states.get(TOTAL).state == "unknown"


async def test_401_refreshes_and_retries(hass, mock_client, config_entry) -> None:
    """A rejected access token is refreshed once, then the poll is retried."""
    await _setup(hass, config_entry)
    calls = {"n": 0}

    def stations():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("Failed to get stations: 401 - unauthorized")
        return [make_station()]

    mock_client.get_stations.side_effect = stations
    refreshes = mock_client.refresh.call_count
    await _poll(hass, config_entry)
    assert mock_client.refresh.call_count == refreshes + 1
    assert hass.states.get(STATUS).state == "idle"


async def test_rotated_refresh_token_is_saved(hass, mock_client, config_entry) -> None:
    """Elli may rotate the refresh token; the new one is persisted."""
    mock_client.refresh.return_value = make_tokens(refresh="refresh-2")
    await _setup(hass, config_entry)
    assert config_entry.data[CONF_REFRESH_TOKEN] == "refresh-2"
    # Updating the entry data must not reload the entry.
    assert config_entry.state is ConfigEntryState.LOADED


async def test_dead_refresh_token_starts_reauth(hass, mock_client, config_entry) -> None:
    """invalid_grant on the refresh token asks the user to log in again."""
    mock_client.refresh.side_effect = ReauthenticationRequired("dead")
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_login_unreachable_retries(hass, mock_client, config_entry) -> None:
    """A transient token endpoint error retries the setup later."""
    mock_client.refresh.side_effect = TokenRefreshError("down")
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_legacy_password_entry_starts_reauth(hass, mock_client) -> None:
    """1.x entries stored email/password; they need the browser login."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="me@example.com",
        data={"email": "me@example.com", "password": "secret"},
    )
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert hass.config_entries.flow.async_progress()[0]["context"]["source"] == "reauth"


async def test_unload_closes_client(hass, mock_client, config_entry) -> None:
    """Unloading closes the HTTP client."""
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    mock_client.close.assert_called()


async def test_only_total_energy_has_statistics(hass, mock_client, config_entry) -> None:
    """Session energy is informational; the total feeds the Energy dashboard."""
    await _setup(hass, config_entry)
    assert hass.states.get(TOTAL).attributes["state_class"] == "total_increasing"
    assert "state_class" not in hass.states.get(SESSION).attributes
