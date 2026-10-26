# Changelog

All notable changes to Pi-hole Extended. Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Downloadable diagnostics with host, password and client identities redacted.
- Client devices Pi-hole no longer reports can be deleted from Home Assistant.
- Architecture notes in `docs/architecture.md`; issue and pull request templates.

### Changed
- Sensor names now come from the translation files, so English installations show English names.
- A persistent unusual-activity evaluation failure is now logged once as a warning.
- Device names from Pi-hole are limited to 100 characters.
- The HTTPS options explain that HTTP sends the password unencrypted.
- Internal refactors: entity ID helpers, the Store and shared validators moved to their own modules; persisted coverage reasons are an enum.

## [0.10.0] - 2026-10-25

### Added
- **Activity card** (`custom:phe-activity-card`), loaded automatically: charts per-device DNS activity from Recorder statistics, with selectable devices, block size (5 min to 1 day), range (6 hours to 30 days), lines or bars, logarithmic scale, hover cursor and tooltip, legend toggles and links to each sensor's history. Configured from the card editor; English and Spanish.
- **Unusual-activity detection**: the card marks sustained spikes against each device's own recent pattern and lists devices that are unusual now, even when they are not in the chart.
- **Unusual activity binary sensor** per device (`binary_sensor.phe_<device>_actividad_inusual`) with current rate, usual rate, threshold, multiple of usual and start time, evaluated every 5 minutes.
- **Sensitivity option** (Off, Low, Medium, High) shared by the card and the sensors.
- `pihole_queries/client_activity` websocket command used by the card.
- Dashboard and automation examples for the card and the sensors.

### Changed
- README rewritten; the old four-view starter dashboard and its screenshots were replaced by the activity-card examples.

## [0.9.0] - 2026-10-25

### Changed
- Detailed query history is retired: the integration no longer keeps individual query rows or recent-query attributes, and removes those fields from its Store when it loads.
- The retention options are renamed **Activity enabled** and **Totals only**.

## [0.8.1] - 2026-10-24

### Fixed
- Restored the source-time rolling one-minute query sensor.

## [0.8.0] - 2026-10-24

### Changed
- The older unversioned allowed/blocked counters are retired; their entity-registry entries and Recorder history are preserved.

### Added
- Documented measured high-volume behavior.

## [0.7.0] - 2026-10-24

### Changed
- Lower memory use when publishing query summaries and better handling of retained client data, with migration and regression coverage.

## [0.6.0] - 2026-10-21

### Added
- Rolling one-minute query counters as total-sensor attributes.
- Readable entity names and stable `phe_`-prefixed entity IDs.

### Fixed
- Query pagination cursor.

### Removed
- Per-second rate sensors, replaced by the one-minute counters.

## [0.5.2] - 2026-10-21

### Changed
- API and Logbook errors are distinguished in the logs.

## [0.5.1] - 2026-10-21

### Changed
- Per-client sensor IDs are prefixed with `phe` for easier Recorder filtering.

## [0.4.1] - 2026-10-21

### Added
- A 1-second polling interval is allowed.

## [0.4.0] - 2026-10-21

### Added
- Per-client per-second query-rate sensors (replaced in 0.6.0).

## [0.3.2] - 2026-10-21

### Fixed
- API session cleanup.
