"""Fixtures for Elli Charger tests."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from elli_client import (
    AuthorizationSession,
    ChargingRecord,
    ChargingSession,
    FirmwareInfo,
    Station,
    TokenResponse,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.elli_charger.const import CONF_REFRESH_TOKEN, DOMAIN

STATION_ID = "st-1"
ACCOUNT_ID = "auth0|account-1"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components in every test."""
    return


def make_id_token(sub: str = ACCOUNT_ID) -> str:
    """Build an unsigned JWT with a sub claim."""

    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part({'sub': sub})}.sig"


def make_tokens(refresh: str = "refresh-1", sub: str = ACCOUNT_ID) -> TokenResponse:
    return TokenResponse(
        access_token="access",
        refresh_token=refresh,
        id_token=make_id_token(sub),
        expires_in=3600,
    )


def make_station(**kwargs) -> Station:
    return Station(
        id=STATION_ID,
        name="Garage",
        serial_number="SN1",
        model="Elli Charger Pro",
        **kwargs,
    )


def make_session(**kwargs) -> ChargingSession:
    data = {
        "id": "sess-active",
        "station_id": STATION_ID,
        "start_date_time": "2026-10-30T08:00:00Z",
        "energy_consumption_wh": 2500,
        "momentary_charging_speed_watts": 11000,
        "lifecycle_state": "active",
        "charging_state": "charging",
    }
    data.update(kwargs)
    return ChargingSession(**data)


def make_record(record_id: str, wh: int, day: int, session_id: str | None = None) -> ChargingRecord:
    start = datetime(2026, 10, day, 18, 0, tzinfo=UTC)
    return ChargingRecord(
        id=record_id,
        created_at=(start + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        total_energy_wh=wh,
        start_date_time=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        stop_date_time=(start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        session_faulted=False,
        charging_session_id=session_id or f"sess-{record_id}",
        transaction_id=f"tx-{record_id}",
        connector_id=1,
        station_id=STATION_ID,
        station_serial_number="SN1",
        station_name="Garage",
        station_model="Elli Charger Pro",
    )


@pytest.fixture
def mock_client():
    """An ElliAPIClient double used by both the flow and the integration."""
    client = MagicMock()
    client.access_token = None

    def set_tokens(tokens):
        client.access_token = tokens.access_token

    client.set_tokens.side_effect = set_tokens
    client.refresh.return_value = make_tokens()
    client.create_authorization.return_value = AuthorizationSession(
        authorization_url="https://login.elli.eco/authorize?x=1",
        state="state-1",
        code_verifier="verifier",
        created_at=datetime.now(UTC),
    )
    client.exchange_callback.return_value = make_tokens()
    client.get_stations.return_value = [make_station()]
    client.get_charging_sessions.return_value = []
    fw = make_station(installed_firmware=FirmwareInfo(id="fw", version="1.2.3"))
    client.get_firmware_info.return_value = [fw]
    client.get_charging_records.return_value = [
        make_record("r1", 10000, 1),
        make_record("r2", 5000, 2),
    ]
    with (
        patch("custom_components.elli_charger.ElliAPIClient", return_value=client),
        patch(
            "custom_components.elli_charger.config_flow.ElliAPIClient",
            return_value=client,
        ),
    ):
        yield client


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Garage",
        unique_id=ACCOUNT_ID,
        data={CONF_REFRESH_TOKEN: "refresh-1"},
    )
