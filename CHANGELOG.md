# Changelog

All notable changes to this maintained fork are documented here.

This project is a maintained fork of [frlequ/homeassistant-mojelektro](https://github.com/frlequ/homeassistant-mojelektro). Upstream authorship and the original MIT license are preserved.

## [Unreleased]

### Long-term statistics

- import the hourly import and export from Moj Elektro's 15-minute readings into long-term statistics, so the history can be used in statistics graphs, Sankey charts and the like; the history is backfilled when the integration is set up (option, default on), topped up daily at 06:17 and can be re-imported from a date with the `mojelektro.import_history` service
- added `Total import` and `Total export` sensors; the imported hourly history is written as their long-term statistics, and they show the latest running total. They have no state class on purpose: the totals arrive a day late, and a state class would make Home Assistant book each update into the hour it arrived in instead of the hours the energy was used

### Reliability

- retry when the API answers HTTP 429 (it allows 5 requests a second per token)

### Device

- the meter is now a regular device named `Moj Elektro <meter id>` instead of a service; sensors are named without the `Moj Elektro` prefix (existing entity ids are unchanged) and an existing service device is replaced on setup

## [0.2.7] - 2026-09-21

First maintained-fork release.

### Home Assistant compatibility

- migrate legacy suffixed sensor unique IDs (for example `_2`, `_3`) in the entity registry before using deterministic IDs, preserving existing entity IDs, history, dashboards and automations
- fixed the device-registry `model` value so it is a string and remains compatible with Home Assistant 2026.12+
- removed synchronous `manifest.json` reads from the Home Assistant event loop
- migrated sensor state handling to `SensorEntity.native_value`
- switched startup to `DataUpdateCoordinator.async_config_entry_first_refresh()`
- updated config-entry unloading to current Home Assistant helpers
- added `strings.json` for current config-flow validation
- refreshed CI for Python 3.14 and Home Assistant 2026.9.x

### Authentication and configuration

- added separate authentication, invalid-request and connection errors
- added Home Assistant reauthentication when an API token expires or is revoked
- added duplicate EIMM protection in the config flow
- migrate legacy config entries to a stable EIMM unique ID when safe
- made entity unique IDs deterministic while preserving the historic ID shape

### API and data robustness

- correct Slovenian network tariff-block hour boundaries and work-free-day handling against the current Energy Agency schedule
- preserve prior sensor values when Moj Elektro temporarily returns an empty successful payload instead of synthesizing zero values
- preserve `TOTAL_INCREASING` statistics from false zero/reset/rebound sequences
- fixed crashes caused by missing or unknown reading types
- fixed accidental list access using index `-1` when a required reading type is absent
- preserve the expected entity set when Moj Elektro temporarily returns partial data
- preserve previous values where appropriate during partial responses
- validate API payload shapes more defensively
- derive validation and tariff reading types from the integration's mapping rather than duplicating IDs
- removed unused `requests` dependency
- removed `dateutil` dependency and use Python's built-in datetime parser

### Contracted power

- included the upstream fix for contracted-power values becoming unavailable on the last day of their validity period

### Documentation and packaging

- changed project documentation to clearly identify this repository as a maintained fork
- updated the Moj Elektro token location to **API storitve**
- changed HACS instructions to use this repository as a custom integration
- updated repository URLs in the integration manifest
- set integration version to `0.2.7`

### Upstream work incorporated or adapted

- [upstream PR #59](https://github.com/frlequ/homeassistant-mojelektro/pull/59) — blocking `manifest.json` file read  
  Adapted and superseded by removing the runtime manifest read entirely.

- [upstream PR #60](https://github.com/frlequ/homeassistant-mojelektro/pull/60) — contracted-power validity on the final day  
  Incorporated into 0.2.7.

- [upstream PR #64](https://github.com/frlequ/homeassistant-mojelektro/pull/64) — new Moj Elektro portal wording  
  Incorporated into the documentation.

### Upstream issues addressed

- [#54](https://github.com/frlequ/homeassistant-mojelektro/issues/54) — blocking call
- [#62](https://github.com/frlequ/homeassistant-mojelektro/issues/62) — database executor / blocking-call warnings
- [#63](https://github.com/frlequ/homeassistant-mojelektro/issues/63) — integration slowdowns caused by blocking I/O
- [#65](https://github.com/frlequ/homeassistant-mojelektro/issues/65) — non-string device model

### Reviewed but intentionally deferred

- [upstream PR #57](https://github.com/frlequ/homeassistant-mojelektro/pull/57) — alternate 15-minute cache window  
  Needs validation against current API behaviour before inclusion.

- [upstream PR #58](https://github.com/frlequ/homeassistant-mojelektro/pull/58) — total meter readings  
  Planned for the 0.3.x API refresh.

- [upstream issue #66](https://github.com/frlequ/homeassistant-mojelektro/issues/66) — souporaba  
  Planned for 0.3.x using the official `GET /souporaba` endpoint.

## Planned: 0.3.x

The next feature series is intended to align the integration more closely with the current official Moj Elektro API.

Planned work includes:

- dynamic reading types from `GET /reading-type`
- reading quality handling from `GET /reading-qualities`
- souporaba support from `GET /souporaba`
- total meter-reading sensors
- improved long-term statistics and Energy Dashboard integration
- useful metering-point metadata from `GET /merilno-mesto/{identifikator}`
- improved contracted-power metadata from `GET /merilna-tocka/{gsrn}`
- automated API/config-flow tests
- Slovenian translations
