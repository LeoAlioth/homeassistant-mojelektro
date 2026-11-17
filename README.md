# Moj Elektro for Home Assistant

[![Version](https://img.shields.io/badge/version-0.3.6-blue.svg)](https://github.com/LeoAlioth/homeassistant-mojelektro)
[![Python check](https://github.com/LeoAlioth/homeassistant-mojelektro/actions/workflows/python_check.yml/badge.svg)](https://github.com/LeoAlioth/homeassistant-mojelektro/actions/workflows/python_check.yml)
[![Release validation](https://github.com/LeoAlioth/homeassistant-mojelektro/actions/workflows/release_validation.yml/badge.svg)](https://github.com/LeoAlioth/homeassistant-mojelektro/actions/workflows/release_validation.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/LeoAlioth/homeassistant-mojelektro)](https://github.com/LeoAlioth/homeassistant-mojelektro/stargazers)

Home Assistant custom integration for electricity-meter data from the Slovenian **Moj Elektro** service using the official Informatika.si API.

> [!IMPORTANT]
> **This repository is a maintained fork of [frlequ/homeassistant-mojelektro](https://github.com/frlequ/homeassistant-mojelektro), by way of [jursko90/homeassistant-mojelektro](https://github.com/jursko90/homeassistant-mojelektro).**
>
> The original project, authorship and MIT license are preserved. This fork exists to keep the integration compatible with current Home Assistant releases, fix outstanding issues and continue development against the current Moj Elektro API.

## Current status

**Maintained fork:** `LeoAlioth/homeassistant-mojelektro`  
**Maintainer:** [`LeoAlioth`](https://github.com/LeoAlioth)  
**Current version:** `0.4.0`  
**Home Assistant baseline:** `2025.1+`  
**API:** `https://api.informatika.si/mojelektro/v1`

The 0.3.x series imports hourly import/export history into long-term statistics and adds total import/export sensors and the current network tariff block sensors. See [CHANGELOG.md](CHANGELOG.md).

## What the integration currently provides

- 15-minute imported and exported energy readings
- daily imported/exported energy
- monthly imported/exported energy
- peak/off-peak tariff readings where available
- daily consumption grouped by network tariff block
- contracted power for blocks 1-5
- network charges for the running month (0.4.0): the monthly peak per block and its headroom against the contracted power, the excess power (root of the summed squared overruns, per the network-charge methodology) with the number of intervals over, an "over agreed power" problem sensor, the last 15-minute import and export power, and the month's agreed power charge and excess power charge in EUR
- Home Assistant UI config flow
- configurable decimal precision
- automatic token reauthentication flow
- stable entities across temporary partial API responses

### Network-charge figures

The figures follow the Slovenian network-charge methodology: a 15-minute interval's power is its energy divided by 0.25 h, a block's reached power is the highest such value in the calendar month, and the excess power is `sqrt(sum((P - agreed)^2))` over the intervals above the agreed power. Pick your **network-charge user group** (0-4, default 0 = households) when adding the integration, or later under *Configure*; it selects the price table.

The **agreed power charge** is the agreed power times each block's monthly power price, in EUR without VAT, summed over the blocks that occur in the month (block 1 only in the higher season, block 5 only in the lower one). It is a fixed monthly amount. For a user with no agreed power the reached peak per block is used instead (attribute `basis`). The **excess power charge** is the weighting factor times the power price times the excess power; the network operator bills it only if the agreed power was changed. Both leave out the energy charge (net metering nets it), the connection-power charge, contributions and VAT. Prices are the Energy Agency's table of 1 March - 31 December 2026 (in `prices.py`; there is no machine-readable source); January and February 2027 use the 2026 prices and say so in the `prices_estimated` attribute, and no charge is shown for months without a table. The data is a day late, so the figures run up to yesterday (`data_through`).

Moj Elektro is **not a real-time source**. Meter readings are typically available with an approximately **24-hour delay**. Data can also be incomplete or temporarily inaccurate while the upstream service aggregates readings, especially during the overnight period (roughly midnight to 06:00). Dashboards and automations should therefore not treat the 15-minute sensors as near-real-time measurements.

## Why this fork exists

The original integration remained useful, but several Home Assistant compatibility issues accumulated while upstream development slowed down.

The first maintained-fork release addresses, among other things:

- blocking file I/O inside the Home Assistant event loop
- Home Assistant 2026.12 device-registry compatibility
- modern `SensorEntity.native_value` usage
- safer `DataUpdateCoordinator` startup and update handling
- proper authentication, connection and API-request error handling
- reauthentication when an API token expires or is revoked
- duplicate EIMM/config-entry protection
- deterministic entity unique IDs
- safer handling of incomplete Moj Elektro API responses
- contracted-power validity on the final day of a validity period
- removal of unused runtime dependencies
- refreshed HACS and CI metadata

See [CHANGELOG.md](CHANGELOG.md) for release details.

## Upstream work reviewed

This fork does not claim the upstream work as new work. Relevant open upstream pull requests and issues were reviewed and either incorporated, adapted or intentionally deferred.

| Upstream item | Contributor | Status in this fork | Notes |
| --- | --- | --- | --- |
| [PR #59](https://github.com/frlequ/homeassistant-mojelektro/pull/59) | [`kosl`](https://github.com/kosl) | **Adapted / superseded** | Fix for blocking `manifest.json` reads. The fork removes the runtime file read entirely and uses the integration version directly. |
| [PR #60](https://github.com/frlequ/homeassistant-mojelektro/pull/60) | [`mikrohard`](https://github.com/mikrohard) | **Included** | Fixes contracted-power sensors on the last day of the validity period by comparing dates instead of full datetimes. |
| [PR #64](https://github.com/frlequ/homeassistant-mojelektro/pull/64) | [`MaJerle`](https://github.com/MaJerle) | **Included** | Documentation updated to the current Moj Elektro portal wording: `API storitve`. |
| [PR #57](https://github.com/frlequ/homeassistant-mojelektro/pull/57) | [`kosl`](https://github.com/kosl) | **Reviewed / deferred** | Changes the 15-minute-data window to avoid incomplete overnight data. This needs validation against the current API before inclusion. |
| [PR #58](https://github.com/frlequ/homeassistant-mojelektro/pull/58) | [`kosl`](https://github.com/kosl) | **Reviewed / planned** | Adds total meter readings. Planned as part of the 0.3.x API refresh rather than mixed into the stability release. |

### Upstream issues addressed in 0.2.7

- [#54 – blocking call](https://github.com/frlequ/homeassistant-mojelektro/issues/54)
- [#62 – blocking/database executor warnings](https://github.com/frlequ/homeassistant-mojelektro/issues/62)
- [#63 – integration slowdowns caused by blocking I/O](https://github.com/frlequ/homeassistant-mojelektro/issues/63)
- [#65 – non-string device model, breaking in HA 2026.12](https://github.com/frlequ/homeassistant-mojelektro/issues/65)

Upstream [issue #66](https://github.com/frlequ/homeassistant-mojelektro/issues/66) requesting **souporaba** data is part of the planned 0.3.x work.

## Installation

### HACS

This fork is installed as a **custom HACS repository**.

1. Open **HACS**.
2. Open the menu and choose **Custom repositories**.
3. Add:

   `https://github.com/LeoAlioth/homeassistant-mojelektro`

4. Select category **Integration**.
5. Install **Moj Elektro**.
6. Restart Home Assistant.

If the original HACS-default integration is already installed, remove or replace it with this repository before testing the maintained fork.

### Manual installation

Copy:

`custom_components/mojelektro`

into your Home Assistant:

`/config/custom_components/mojelektro`

Then restart Home Assistant.

## Moj Elektro API setup

Official API documentation:

https://docs.informatika.si/mojelektro/api/

1. Sign in to **Moj Elektro**.
2. Open **API storitve**.
3. Create an API token with the desired expiration.
4. Copy the generated token.
5. Find the required **EIMM** identifier under your metering-point information.
6. In Home Assistant open **Settings → Devices & services → Add integration → Moj Elektro**.
7. Enter the token and EIMM.

No `configuration.yaml` entry is required.

## Roadmap

### 0.2.x — compatibility and stability

- [x] Home Assistant 2026 compatibility
- [x] remove event-loop blocking file reads
- [x] fix device-registry model type
- [x] modern sensor API
- [x] robust coordinator/error handling
- [x] token reauthentication
- [x] stable config-entry and entity IDs
- [x] contracted-power validity boundary fix
- [x] safer partial/malformed API-response handling
- [ ] add automated unit tests for API parsing and config flow
- [ ] validate CI/HACS workflows on the fork

### 0.3.x — Moj Elektro API refresh

- [ ] use official `/reading-type` instead of maintaining the full reading-type list manually
- [ ] use `/reading-qualities` to expose or reject low-quality readings correctly
- [ ] add `/souporaba` support
- [ ] add total meter-reading sensors
- [ ] improve long-term statistics / Energy Dashboard support
- [ ] expose useful metering-point metadata from `/merilno-mesto/{identifikator}`
- [ ] improve contracted-power data from `/merilna-tocka/{gsrn}`
- [ ] review the 15-minute-data window against current API behaviour
- [ ] add Slovenian translations

### Later

- [ ] diagnostics download suitable for bug reports without exposing the API token
- [ ] broader automated test coverage against recorded/sanitized API payloads
- [ ] release automation and tagged HACS releases
- [ ] continue tracking upstream changes where useful

## Relationship to the original project

The original repository is:

**[frlequ/homeassistant-mojelektro](https://github.com/frlequ/homeassistant-mojelektro)**

This maintained fork builds on that work and remains licensed under the same **MIT License**. Original copyright and license notices are retained.

Where upstream pull requests or fixes are incorporated, they are documented above and in the changelog.

## Support the original author

The original integration was created and maintained by **frlequ**. If the original project has been useful to you, you can support its author here:

<a href="https://www.buymeacoffee.com/frlequ"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="50"></a>

## Issues and contributions

For problems specific to this maintained fork, use the fork issue tracker:

https://github.com/LeoAlioth/homeassistant-mojelektro/issues

When reporting a problem, please include:

- Home Assistant version
- Moj Elektro integration version
- relevant Home Assistant log messages
- whether the problem also occurs after restarting Home Assistant

**Never post your Moj Elektro API token in an issue or log excerpt.**

Pull requests are welcome. Changes should remain focused, Home Assistant-compatible and avoid exposing credentials or personal metering data.

## License

MIT License. See [LICENSE](LICENSE).

This fork preserves the license and attribution of the original project.
