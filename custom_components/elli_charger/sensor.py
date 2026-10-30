"""Sensors for the Elli Charger integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    STATUS_CHARGING,
    STATUS_CONNECTED,
    STATUS_IDLE,
    STATUS_OPTIONS,
)
from .coordinator import ElliConfigEntry, ElliData, ElliDataUpdateCoordinator

PARALLEL_UPDATES = 0


def _status(data: ElliData, station_id: str) -> str:
    session = data.sessions.get(station_id)
    if session is None:
        return STATUS_IDLE
    if (session.momentary_charging_speed_watts or 0) > 0 or (
        session.charging_state and "charging" in session.charging_state.lower()
    ):
        return STATUS_CHARGING
    return STATUS_CONNECTED


def _power(data: ElliData, station_id: str) -> int:
    session = data.sessions.get(station_id)
    if session is None:
        return 0
    return session.momentary_charging_speed_watts or 0


def _session_energy(data: ElliData, station_id: str) -> float | None:
    """Energy of the running session, else of the last completed one."""
    session = data.sessions.get(station_id)
    if session is not None and session.energy_consumption_wh is not None:
        return session.energy_consumption_wh / 1000
    record = data.last_records.get(station_id)
    if record is not None:
        return record.total_energy_wh / 1000
    return None


def _total_energy(data: ElliData, station_id: str) -> float | None:
    total = data.total_energy_wh.get(station_id)
    return None if total is None else total / 1000


def _last_session(data: ElliData, station_id: str) -> str | None:
    session = data.sessions.get(station_id)
    if session is not None:
        return session.charging_state or session.lifecycle_state or STATUS_CONNECTED
    return STATUS_IDLE


def _last_session_attrs(data: ElliData, station_id: str) -> dict[str, Any]:
    session = data.sessions.get(station_id)
    if session is not None:
        return {
            "session_id": session.id,
            "lifecycle_state": session.lifecycle_state,
            "charging_state": session.charging_state,
            "authentication_method": session.authentication_method,
            "rfid_card": session.rfid_card_serial_number,
            "start_time": session.start_date_time,
            "last_updated": session.last_updated,
        }
    record = data.last_records.get(station_id)
    if record is not None:
        return {
            "session_id": record.charging_session_id,
            "energy_kwh": record.total_energy_wh / 1000,
            "rfid_card": record.rfid_card_serial_number,
            "start_time": record.start_date_time,
            "end_time": record.stop_date_time,
            "faulted": record.session_faulted,
        }
    return {}


@dataclass(frozen=True, kw_only=True)
class ElliSensorDescription(SensorEntityDescription):
    """Describes an Elli sensor."""

    # Suffix appended to the 1.x unique ID scheme, kept for existing entities.
    unique_id_suffix: str
    value_fn: Callable[[ElliData, str], Any]
    attrs_fn: Callable[[ElliData, str], dict[str, Any]] | None = None


SENSORS: tuple[ElliSensorDescription, ...] = (
    ElliSensorDescription(
        key="status",
        translation_key="status",
        unique_id_suffix="",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        value_fn=_status,
    ),
    ElliSensorDescription(
        key="power",
        translation_key="power",
        unique_id_suffix="_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=_power,
    ),
    ElliSensorDescription(
        key="session_energy",
        translation_key="session_energy",
        unique_id_suffix="_session_energy",
        device_class=SensorDeviceClass.ENERGY,
        # No state class: it falls back to the last completed session when
        # idle, so statistics would count session ends as meter resets. The
        # total energy sensor is the one for the Energy dashboard.
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_session_energy,
    ),
    ElliSensorDescription(
        key="total_energy",
        translation_key="total_energy",
        unique_id_suffix="_total_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_total_energy,
    ),
    ElliSensorDescription(
        key="last_session",
        translation_key="last_session",
        unique_id_suffix="_last_session",
        value_fn=_last_session,
        attrs_fn=_last_session_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElliConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for every wallbox on the account."""
    coordinator = entry.runtime_data
    async_add_entities(
        ElliSensor(coordinator, station_id, description)
        for station_id in coordinator.data.stations
        for description in SENSORS
    )


class ElliSensor(CoordinatorEntity[ElliDataUpdateCoordinator], SensorEntity):
    """One value of one wallbox."""

    _attr_has_entity_name = True
    entity_description: ElliSensorDescription

    def __init__(
        self,
        coordinator: ElliDataUpdateCoordinator,
        station_id: str,
        description: ElliSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._station_id = station_id
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_wallbox_{station_id}"
            f"{description.unique_id_suffix}"
        )
        station = coordinator.data.stations[station_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, station_id)},
            manufacturer="Elli",
            model=station.model,
            name=station.name,
            serial_number=station.serial_number,
            sw_version=station.firmware_version,
        )

    @property
    def available(self) -> bool:
        """Return True while the account still lists this wallbox."""
        return super().available and self._station_id in self.coordinator.data.stations

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data, self._station_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes, if this sensor has any."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data, self._station_id)
