# Nedorachio

Home-Assistant-controlled irrigation controller built on an 8-relay ESP32-WROOM-32E
development board running ESPHome. Replaces a Rachio. See
`docs/superpowers/specs/2026-04-30-nedorachio-irrigation-controller-design.md`
for the full design.

## Status

Implementation in progress. See `docs/superpowers/plans/`.

## Layout

- `firmware/` — ESPHome configuration.
- `homeassistant/packages/` — Home Assistant Package YAML (weather feeder, notifications, dashboard).
- `docs/` — design spec and implementation plan.

## Hardware

Bill of materials, GPIO map, and calibration values are documented in this
section once the bench-prep phase is complete. Until then, the firmware uses
**placeholder GPIO assignments** that need to be reconciled with the real board:

| Function           | Placeholder GPIO | Notes |
|--------------------|------------------|-------|
| Relay 1 (zone 1)   | GPIO23           | active-low (`inverted: true`) — typical for opto-isolated relay boards |
| Relay 2 (zone 2)   | GPIO19           | active-low |
| Relay 3 (zone 3)   | GPIO18           | active-low |
| Relay 4 (zone 4)   | GPIO5            | active-low |
| Relay 5 (zone 5)   | GPIO17           | unused, terminal-blocked for future |
| Relay 6 (zone 6)   | GPIO16           | unused |
| Relay 7 (zone 7)   | GPIO4            | unused |
| Relay 8 (zone 8)   | GPIO13           | unused |
| Rain sensor input  | GPIO27           | internal pull-up |
| Flow meter pulse   | GPIO26           | internal pull-up, interrupt-capable |
| Pressure ADC       | GPIO34           | ADC1, 11 dB attenuation; voltage-divider midpoint |

After unboxing the actual board (Amazon ASIN B0DK6QKNBM), verify each pin with
the multimeter / clicking-relay procedure described in the implementation plan
(Task 1.1) and update `firmware/packages/02-zones.yaml`,
`firmware/packages/03-sensors.yaml`, and this table.

## Home Assistant setup

1. Copy `homeassistant/packages/nedorachio.yaml` into your HA config under
   `packages/`. Make sure `configuration.yaml` has
   `homeassistant: packages: !include_dir_named packages`.
2. Replace `weather.your_local_forecast` with your weather entity and
   `sensor.rain_observed_48h` with whatever observed-rain sensor you have.
   If you don't have one, the feeder writes `0` and the device falls back to
   the rain sensor and static-pressure gate.
3. Reload automations. Within 10 minutes, `number.nedorachio_rain_mm_last_48h`
   should be populated.
