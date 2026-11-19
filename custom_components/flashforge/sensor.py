"""Add Flashforge sensors."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from ffpp.Printer import Printer
    from ffpp.Printer import temperatures as Tool  # noqa: N812
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data_update_coordinator import FlashForgeDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlashforgeSensorEntityDescription(SensorEntityDescription):
    """Sensor entity description with added value fnc."""

    value_fnc: Callable[[Printer], str | float | None] | None = None


@dataclass(frozen=True)
class FlashforgeTempSensorEntityDescription(FlashforgeSensorEntityDescription):
    """Sensor entity description for temperature sensors."""

    value_fnc: Callable[[Tool], float] | None = None


@dataclass(frozen=True)
class FlashforgeJobSensorEntityDescription(FlashforgeSensorEntityDescription):
    """Sensor entity description for values estimated by the coordinator."""

    value_fnc: (
        Callable[[FlashForgeDataUpdateCoordinator], int | datetime | None] | None
    ) = None


SENSORS: tuple[FlashforgeSensorEntityDescription, ...] = (
    FlashforgeSensorEntityDescription(
        key="status",
        icon="mdi:printer-3d",
        value_fnc=lambda printer: printer.machine_status,
    ),
    FlashforgeSensorEntityDescription(
        key="job_percentage",
        icon="mdi:file-percent",
        native_unit_of_measurement=PERCENTAGE,
        value_fnc=lambda printer: printer.print_percent,
    ),
    FlashforgeSensorEntityDescription(
        key="file",
        icon="mdi:file-cad",
        value_fnc=lambda printer: printer.job_file,
    ),
    FlashforgeSensorEntityDescription(
        key="layers",
        icon="mdi:layers-triple",
        value_fnc=lambda printer: printer.job_layers,
    ),
    FlashforgeSensorEntityDescription(
        key="print_layer",
        icon="mdi:layers-edit",
        value_fnc=lambda printer: printer.print_layer,
    ),
    FlashforgeSensorEntityDescription(
        key="print_status",
        icon="mdi:printer-3d",
        value_fnc=lambda printer: printer.status,
    ),
    FlashforgeSensorEntityDescription(
        key="move_mode",
        icon="mdi:move-resize",
        value_fnc=lambda printer: printer.move_mode,
    ),
)
JOB_SENSORS: tuple[FlashforgeJobSensorEntityDescription, ...] = (
    FlashforgeJobSensorEntityDescription(
        key="job_remaining",
        icon="mdi:timer-sand",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fnc=lambda coordinator: coordinator.job_remaining,
    ),
    FlashforgeJobSensorEntityDescription(
        key="job_end",
        icon="mdi:clock-end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fnc=lambda coordinator: coordinator.job_end,
    ),
)
TEMP_SENSORS: tuple[FlashforgeSensorEntityDescription, ...] = (
    FlashforgeTempSensorEntityDescription(
        key="_current",
        value_fnc=lambda tool: tool.now,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    FlashforgeTempSensorEntityDescription(
        key="_target",
        value_fnc=lambda tool: tool.target,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the available FlashForge sensors platform."""
    _LOGGER.debug("async_setup_entry- sensors")
    coordinator: FlashForgeDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]
    entities: list[SensorEntity] = []

    if coordinator.printer.connected:
        # Loop all extruders and add current and target temp sensors.
        for i, tool in enumerate(coordinator.printer.extruder_tools):
            name = (
                f"extruder{i}"
                if len(coordinator.printer.extruder_tools) > 1
                else "extruder"
            )
            for description in TEMP_SENSORS:
                sensor = FlashForgeTempSensor(
                    coordinator=coordinator,
                    description=description,
                    name=name,
                    tool_name=tool.name,
                )
                entities.append(sensor)

        # Loop all beds and add current and target temp sensors.
        for i, tool in enumerate(coordinator.printer.bed_tools):
            name = f"bed{i}" if len(coordinator.printer.bed_tools) > 1 else "bed"
            for description in TEMP_SENSORS:
                sensor = FlashForgeTempSensor(
                    coordinator=coordinator,
                    description=description,
                    name=name,
                    tool_name=tool.name,
                )
                entities.append(sensor)

    for description in SENSORS:
        _LOGGER.debug(f"setup {description}")  # noqa: G004
        entities.append(
            FlashForgeSensor(
                coordinator=coordinator,
                description=description,
            )
        )

    entities.extend(
        FlashForgeJobSensor(coordinator=coordinator, description=description)
        for description in JOB_SENSORS
    )

    async_add_entities(entities)


class FlashForgeSensor(CoordinatorEntity, SensorEntity):
    """Representation of an FlashForge sensor."""

    coordinator: FlashForgeDataUpdateCoordinator
    entity_description: FlashforgeSensorEntityDescription

    def __init__(
        self,
        coordinator: FlashForgeDataUpdateCoordinator,
        description: FlashforgeSensorEntityDescription,
        name: str = "",
        tool_name: str | None = None,
    ) -> None:
        """Initialize a new Flashforge sensor."""
        super().__init__(coordinator)
        self._device_id = coordinator.config_entry.unique_id
        self._attr_device_info = coordinator.device_info
        self.entity_description = description
        self._attr_name = (
            f"{coordinator.config_entry.title}"
            f" {name.title()}"
            f"{description.key.replace('_', ' ').title()}"
        )
        self._attr_unique_id = (
            f"{coordinator.config_entry.unique_id}_{name}{description.key}"
        )

        self.tool_name = tool_name

    @property
    def native_value(self) -> str | float | datetime | None:
        """Return sensor state."""
        if self.entity_description.value_fnc is None:
            return None

        if isinstance(self.entity_description, FlashforgeSensorEntityDescription):
            # If it's a normal sensor we can just pass the printer
            return self.entity_description.value_fnc(self.coordinator.printer)
        return None


class FlashForgeTempSensor(FlashForgeSensor):
    """Representation of an FlashForge temperature sensor."""

    entity_description: FlashforgeTempSensorEntityDescription

    @property
    def native_value(self) -> float | None:
        """Return sensor state."""
        if self.entity_description.value_fnc is None:
            return None
        if self.tool_name:
            # If toolname is set we need to get that tool and pass it to the lambda.
            tool = self.coordinator.printer.extruder_tools.get(self.tool_name)
            if tool is None:
                tool = self.coordinator.printer.bed_tools.get(self.tool_name)
            if tool is not None:
                return self.entity_description.value_fnc(tool)

        return None


class FlashForgeJobSensor(FlashForgeSensor):
    """Representation of a value estimated by the coordinator."""

    entity_description: FlashforgeJobSensorEntityDescription

    @property
    def native_value(self) -> int | datetime | None:
        """Return sensor state."""
        if self.entity_description.value_fnc is None:
            return None
        return self.entity_description.value_fnc(self.coordinator)
