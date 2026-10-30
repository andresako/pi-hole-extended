# Changelog

All notable changes to Pi-hole Extended. Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `custom:phe-live-card`: a live card for one device with a rate gauge (usual range and alert threshold), an allowed/blocked/provisional ring, 60-second bars and a status dot. It is configured from the card editor, which can show or hide each part, and finds the device's other entities from its DNS queries counter.

### Changed
- The dashboard example's per-device view is a normal view with the live card instead of a generated one.

### Removed
- The `custom:phe-device` view strategy added in 0.12.0. Its generated views could not be edited; use the live card in a view of your own.

## [0.12.0] - 2026-10-30

### Added
- Per-device view: `strategy: type: custom:phe-device` (with `entity` or `device`) generates a view for one device with its unusual-activity verdict, activity chart, allowed/blocked/provisional counts and the data coverage of its Pi-hole entry. Included as a third view in the dashboard example.

## [0.11.2] - 2026-10-30

### Fixed
- The activity card showed "Custom element doesn't exist" when added to a dashboard. The card script is loaded before the Home Assistant frontend installs its element registry, so the card is now registered again until dashboards can see it.

## [0.11.1] - 2026-10-26

### Changed
- New integration icon with a transparent background and a 512 px `icon@2x.png`.
- Development dependencies and the checkout workflow action updated.

## [0.11.0] - 2026-10-26

### Added
- Downloadable diagnostics with host, password and client identities redacted.
- Client devices that Pi-hole no longer reports can be deleted from Home Assistant.
- Architecture notes (`docs/architecture.md`), issue and pull request templates, and instructions for testing against newer Home Assistant versions.
- Ruff configuration and a Dependabot configuration for GitHub Actions and Python dependencies.
- More activity card tests.

### Changed
- Sensor names now come from the translation files, so English installations show English names.
- A persistent unusual-activity evaluation failure is logged once as a warning instead of at debug level.
- Device names from Pi-hole are limited to 100 characters.
- The HTTPS options explain that plain HTTP sends the password unencrypted.
- Workflow actions are pinned to commit SHAs.
- `SECURITY.md`, `CONTRIBUTING.md` and `docs/development.md` brought up to date.
- Internal refactors: entity ID helpers (`entity_ids.py`), the Store (`store.py`) and shared validators (`validation.py`) moved out of `__init__.py` and the coordinator; persisted coverage reasons are an enum; the coordinator rolls back through a single checkpoint object.
- Verified with Home Assistant 2026.2.3 on Python 3.13 in addition to the 2025.1.0 baseline.

### Removed
- Unused code: an unused lock in the API client and unused imports.

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
