"""Data update coordinator for the Elli Charger integration."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from elli_client import (
    ChargingRecord,
    ChargingSession,
    ElliAPIClient,
    ElliClientError,
    ReauthenticationRequired,
    Station,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    RECORDS_FULL_RESYNC_EVERY_N_POLLS,
    RECORDS_PAGE_SIZE,
    TOKEN_EXPIRY_MARGIN_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

# elli-client raises a bare ValueError("Failed to ...: <status> - <body>") for
# every non-200 answer of the data endpoints.
_STATUS_RE = re.compile(r":\s*(\d{3})\s*-")

# When asking only for new records, start this far before the newest one we
# know, so a record written late (or with a skewed clock) is not missed.
_INCREMENTAL_OVERLAP = timedelta(days=2)

type ElliConfigEntry = ConfigEntry[ElliDataUpdateCoordinator]


def http_status(err: Exception) -> int | None:
    """Return the HTTP status code elli-client put into an error, if any."""
    match = _STATUS_RE.search(str(err))
    return int(match.group(1)) if match else None


class ApiUnauthorized(Exception):
    """The Elli API rejected the access token."""


@dataclass
class ElliData:
    """Everything the sensors need, keyed by station ID."""

    stations: dict[str, Station] = field(default_factory=dict)
    sessions: dict[str, ChargingSession] = field(default_factory=dict)
    last_records: dict[str, ChargingRecord] = field(default_factory=dict)
    total_energy_wh: dict[str, int] = field(default_factory=dict)


class ElliDataUpdateCoordinator(DataUpdateCoordinator[ElliData]):
    """Poll the Elli cloud and keep the token pair fresh."""

    config_entry: ElliConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ElliConfigEntry, client: ElliAPIClient) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
            ),
        )
        self.client = client
        self._refresh_token: str = entry.data[CONF_REFRESH_TOKEN]
        self._token_expires_at: datetime | None = None
        self._poll_count = 0
        # Completed charging records per station, keyed by record ID. None
        # until the first complete read succeeded for that station.
        self._records: dict[str, dict[str, ChargingRecord]] = {}
        self._firmware: dict[str, str] = {}
        self._totals: dict[str, int] = {}

    async def async_refresh_tokens(self) -> None:
        """Get a new access token, persisting a rotated refresh token.

        Raises ReauthenticationRequired when the refresh token is dead.
        """
        tokens = await self.hass.async_add_executor_job(self.client.refresh, self._refresh_token)
        self.client.set_tokens(tokens)
        self._token_expires_at = tokens.expires_at
        if tokens.refresh_token and tokens.refresh_token != self._refresh_token:
            self._refresh_token = tokens.refresh_token
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={
                    **self.config_entry.data,
                    CONF_REFRESH_TOKEN: tokens.refresh_token,
                },
            )

    def _token_expiring(self) -> bool:
        if not self.client.access_token or self._token_expires_at is None:
            return True
        margin = timedelta(seconds=TOKEN_EXPIRY_MARGIN_SECONDS)
        return datetime.now(UTC) >= self._token_expires_at - margin

    async def _async_refresh_or_fail(self) -> None:
        try:
            await self.async_refresh_tokens()
        except ReauthenticationRequired as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ElliClientError as err:
            raise UpdateFailed(f"Could not refresh the Elli access token: {err}") from err

    async def _async_update_data(self) -> ElliData:
        """Fetch stations, the active sessions and the charging records."""
        if self._token_expiring():
            await self._async_refresh_or_fail()

        full_resync = self._poll_count % RECORDS_FULL_RESYNC_EVERY_N_POLLS == 0
        try:
            data = await self.hass.async_add_executor_job(self._fetch, full_resync)
        except ApiUnauthorized:
            # The token was revoked or expired early: refresh once and retry.
            await self._async_refresh_or_fail()
            try:
                data = await self.hass.async_add_executor_job(self._fetch, full_resync)
            except ApiUnauthorized as err:
                raise UpdateFailed("The Elli API rejected a fresh access token") from err
        self._poll_count += 1
        return data

    def _call(self, func, *args, **kwargs):
        """Run one elli-client data call, translating its errors."""
        try:
            return func(*args, **kwargs)
        except ValueError as err:
            status = http_status(err)
            if status == 401:
                raise ApiUnauthorized from err
            raise UpdateFailed(str(err)) from err
        except Exception as err:  # httpx network errors and the like
            raise UpdateFailed(f"Error talking to the Elli API: {err}") from err

    def _fetch(self, full_resync: bool) -> ElliData:
        """Blocking part of an update; runs in the executor."""
        stations = {s.id: s for s in self._call(self.client.get_stations)}

        try:
            sessions = self._call(self.client.get_charging_sessions)
        except UpdateFailed as err:
            # Some accounts answer 404 RESOURCE_NOT_FOUND here (seen with no
            # active session and on some Charger Connect Gen 1 boxes). Treat
            # it as "nothing is charging" instead of failing the whole update.
            if http_status(err.__cause__) != 404:
                raise
            sessions = []
        active: dict[str, ChargingSession] = {}
        for session in sessions:
            if session.station_id in stations and session.station_id not in active:
                active[session.station_id] = session

        if full_resync or not self._firmware:
            try:
                for fw_station in self._call(self.client.get_firmware_info):
                    if fw_station.installed_firmware:
                        self._firmware[fw_station.id] = fw_station.installed_firmware.version
            except UpdateFailed as err:
                _LOGGER.debug("Could not read Elli firmware info: %s", err)
        for station_id, version in self._firmware.items():
            if station_id in stations:
                stations[station_id].firmware_version = version

        for station_id in stations:
            try:
                self._update_records(station_id, full_resync)
            except UpdateFailed as err:
                # Keep the records we already have; the totals then stay put
                # instead of dropping, which the Energy dashboard would read
                # as a meter reset.
                _LOGGER.warning("Could not read Elli charging records: %s", err)

        data = ElliData(stations=stations, sessions=active)
        for station_id in stations:
            records = self._records.get(station_id)
            if records is None:
                continue
            if records:
                data.last_records[station_id] = max(
                    records.values(), key=lambda r: r.stop_date_time
                )
            total = sum(r.total_energy_wh for r in records.values())
            session = active.get(station_id)
            billed_sessions = {r.charging_session_id for r in records.values()}
            if (
                session is not None
                and session.energy_consumption_wh
                and session.id not in billed_sessions
            ):
                total += session.energy_consumption_wh
            # Between the end of a session and its record appearing, the
            # computed total briefly lacks that session. Never go backwards.
            total = max(total, self._totals.get(station_id, 0))
            self._totals[station_id] = total
            data.total_energy_wh[station_id] = total
        return data

    def _update_records(self, station_id: str, full_resync: bool) -> None:
        """Read completed charging records; all pages or none."""
        known = self._records.get(station_id)
        created_after: str | None = None
        if known and not full_resync:
            newest = max(r.created_at for r in known.values())
            since = _parse_time(newest) - _INCREMENTAL_OVERLAP
            created_after = since.strftime("%Y-%m-%dT%H:%M:%SZ")

        fetched: dict[str, ChargingRecord] = {}
        offset = 0
        while True:
            try:
                page = self._call(
                    self.client.get_charging_records,
                    station_id,
                    created_after=created_after,
                    limit=RECORDS_PAGE_SIZE,
                    offset=offset,
                )
            except UpdateFailed as err:
                if http_status(err.__cause__) != 404:
                    raise
                page = []
            for record in page:
                fetched[record.id] = record
            if len(page) < RECORDS_PAGE_SIZE:
                break
            offset += RECORDS_PAGE_SIZE

        if created_after is None:
            self._records[station_id] = fetched
        else:
            self._records[station_id] = {**(known or {}), **fetched}


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
