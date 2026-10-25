# Pi-hole Extended for Home Assistant

A standalone custom integration for Pi-hole v6. It polls Pi-hole's official `GET /api/queries` endpoint, records newly observed DNS queries in Home Assistant Activity, and creates one device per client.

The minimum Home Assistant version is **2025.1.0** (`hacs.json`). The query API contract has been compared with Pi-hole FTL **6.1.0–6.8.x**; the exact API schema is versioned and each server exposes its own documentation at `/api/docs`. The native dashboard was exercised on Home Assistant Core 2025.1.0. These checks do not establish behavior on later Home Assistant versions or FTL releases outside that range.

Documentation: [English](../README.md) · [Español](../README.es.md)

## Features

- Configure host, port, HTTPS, Pi-hole application password, and polling interval in Home Assistant.
- Record the query time, client name/IP, domain, result/status, query type, reply, and other fields when Pi-hole provides them.
- Resume using Pi-hole's query IDs and API cursor, avoiding normal duplicate entries across polling cycles and Home Assistant restarts.
- Create a per-client device with five sensors: cumulative total queries, a source-time rolling minute count, and cumulative allowed/blocked/provisional counts for the active classifier version. The total sensor also exposes HA-observation rolling metrics as attributes. Existing legacy allowed/blocked entities and the old classified minute-rate entities are archived by the integration; their registry IDs and Recorder history are retained.
- Use short, readable names and stable `phe_` IDs for per-client sensors. Diagnostic sensor IDs are scoped to the integration entry. The total sensor exposes client identity and numeric counters.
- Request DNS entries through Home Assistant Activity/Logbook; Recorder retention depends on its configured filters and policy.
- Operate independently of Home Assistant's built-in Pi-hole integration.

## Installation

### HACS

The repository is [andresako/pi-hole-extended](https://github.com/andresako/pi-hole-extended). It can be added as a custom repository through **HACS → Integrations → ⋮ → Custom repositories**, category **Integration**. Inclusion in HACS's default catalog is paused until the integration has been verified; the earlier request was closed.

### Manual

Copy `custom_components/pihole_queries` into the `custom_components` directory under your Home Assistant configuration directory, restart Home Assistant, then use **Settings → Devices & services → Add integration** and select **Pi-hole Extended**.

## Configuration

The config flow requests:

- Bare hostname or IP address (no scheme or path) and web/API port.
- Whether to use HTTPS.
- Whether to verify the HTTPS certificate. Keep this enabled for trusted certificates; disable it only for a self-signed certificate.
- A Pi-hole application password.
- Poll interval from 1 to 3600 seconds (default: 15 seconds).

Create an application password from the Pi-hole web interface Settings page and enter it in Home Assistant. Pi-hole displays an application password only once. It is stored in the config entry and is not part of source files, examples, or integration logs. Pi-hole v6 uses a temporary SID; the integration obtains one through `POST /api/auth` and renews it after an expired-session response.

Use Home Assistant's **Reconfigure** action to change the connection. The flow validates the selected endpoint and credentials before saving. It uses the explicit port as entered; switching HTTPS does not rewrite a custom port. Select **Same Pi-hole** to preserve the source identity, cursor, and existing client entities when changing address, protocol, or port. Select **Replacement Pi-hole** only when the server itself was replaced: this creates a fresh source generation and Store baseline, disables the previous generation's client sensors, and leaves their Recorder history intact. The replacement password is optional; leaving it blank keeps the stored password. A second entry cannot use the same normalized host, protocol, and port.

In the integration's **Options**, choose the Activity policy (**Activity enabled** or **Totals only**) and Activity events (`all`, `blocked`, or `none`). The default allows Activity entries for all events. The Activity setting filters only new Activity entries; it never filters query totals. Totals only suppresses all new Activity entries regardless of that setting. The integration stores client identity, cumulative numeric totals, and bounded per-second counters for rolling rates. It does not retain individual query details in its Store or sensor attributes.

At a 1-second interval, normal operation makes about 60 authenticated `GET /api/queries` requests per minute, reusing one SID; it does not log in for each poll. Catching up after a backlog can require additional paginated requests. Pi-hole documents login-attempt throttling for `POST /api/auth`, but does not publish a numeric limit for normal authenticated query reads. No supported request threshold is documented, so 1 second is an optional high-frequency setting, not a guaranteed safe maximum. Start at 5 seconds if Pi-hole is a low-power device and watch its load. Separately, Pi-hole FTL versions before 6.7 have an upstream web/API denial-of-service issue involving uncontrolled request floods; see the [Pi-hole FTL security advisory](https://github.com/pi-hole/FTL/security/advisories/GHSA-q6fm-xwxf-37r5) and update FTL to 6.7 or later before exposing its web/API service to untrusted networks.

## Query history and limits

The integration calls Home Assistant's documented `logbook.log` action and tags each entry with the `pihole_queries` domain. The original Pi-hole timestamp is included in each message. A successful API read or return from `logbook.log` does not verify that Activity or Recorder retained an entry. Recorder filters and retention determine what is stored and for how long; this integration does not inspect Recorder's private data. If Activity uses an include filter, include the `pihole_queries` domain. A high DNS query rate can produce substantial Recorder and Logbook data.

### Measured high-volume behavior

The integration keeps bounded numeric buckets for rolling rates and does not store a row per query in its Store or sensor attributes. With Activity enabled, it can send one Activity entry for each new query, which may increase Activity/Recorder volume. The Activity option can disable these entries or restrict them to blocked queries.

Sensor and device names use Home Assistant's English and Spanish integration translations. Activity entries are different: `logbook.log` accepts a finished message string, not an integration translation key, so an existing entry cannot be translated for each viewer. The current event text is Spanish. Sensor attribute names and values are also plain data; Home Assistant's frontend does not currently apply integration translations to arbitrary sensor attributes. The integration's entity names remain localized.

Each discovered Pi-hole client gets five sensors: cumulative DNS queries, the source-time rolling count for the last minute, and cumulative allowed, blocked, and provisional counts for the active classifier. Classifier version remains available as `active_classifier_version` on the cumulative sensor; it is not part of the visible label. The previous unversioned allowed/blocked counters are retired. On an existing installation, their registry entries are disabled by the integration without changing their unique IDs, custom entity IDs, or custom names; their existing Recorder history remains under Home Assistant's normal retention rules. The two previous allowed/blocked last-minute entries are archived the same way because current class rates use HA observation time. User-disabled entities remain user-disabled. The source-time sensor uses Pi-hole `query.time` when valid and falls back to HA observation time when missing or invalid, matching the original window contract. Rolling rates use anonymous one-second numeric buckets and may differ by up to one second at the boundary. The source-time sensor is unavailable during API errors or stale samples, when coverage is unknown or partial, and until more than 60 seconds of complete observation are available after startup. The cumulative sensor retains a separate `queries_observed_last_minute` attribute for queries newly observed by HA. These rolling values do not prove continuous query coverage. At poll intervals above 60 seconds, samples may be stale until the next poll. The polling interval controls update frequency and delay. Entity names describe the metric in plain language, while per-client technical IDs retain the `phe_` prefix. The total sensor keeps client name/IP and numeric counters in its attributes. Existing client devices remain available if a client is removed from Pi-hole; a new client appears after its first query is detected. The initial snapshot discovers clients but does not count its existing queries.

The integration also creates four diagnostic sensors per Pi-hole entry: communication status, last successful read, last query observed, and ingestion coverage. The API status is `error` when the latest query request failed, including authentication failure. After a successful API response, it is `connected` through twice the configured poll interval and `stale` after that. A later Logbook or Store failure does not change this API status. The last-query sample age uses the timestamp Pi-hole supplied; it is unknown when that timestamp is missing, invalid, or in the future. Ingestion coverage reports what the API pagination and saved cursor can establish. It does not mean that Activity or Recorder persisted the entries. Those persistence and retention states remain unverified here. The last-successful-read diagnostic is session-local and becomes unknown after a Home Assistant restart until a successful API update completes.

Per-client sensor entity IDs start with `sensor.phe_` (for example, `sensor.phe_<client>_...`; the entity registry supplies the actual ID). Diagnostic entity IDs are scoped to the integration entry and do not use this prefix. The prefix makes client sensors easy to find and filter in Recorder with `entity_globs`. If you already use a `recorder.include` block, you can add:

```yaml
recorder:
  include:
    entity_globs:
      - sensor.phe_*
```

Keep in mind that `recorder.include` makes Recorder save only included entities. Version 0.6.0 normalizes per-client entity IDs under the `phe_` prefix and removes obsolete per-second rate entities. Custom entity IDs may be changed to the normalized format. Recorder history is preserved. Update any dashboard or automation references that used an old ID after the migration.

The first setup stores a baseline ID and does not backfill older Pi-hole queries. The integration reads the in-memory query history exposed by Pi-hole. Queries no longer available there cannot be recovered after a long outage or an FTL history reset. If Pi-hole's database is recreated and query IDs fall below the saved cursor, the integration pauses ingestion to avoid mixing incompatible histories. Use **Reconfigure** and explicitly choose **Replacement Pi-hole** to resume with a new source generation and baseline; old client sensors are disabled and their Recorder history is retained. Pagination fetches backlogs in 1,000-row pages. A very large backlog can extend a polling cycle. If Home Assistant stops after writing a Logbook entry but before persisting its cursor, the last entry may be replayed on restart because Recorder and integration storage do not share an atomic transaction.

The API schema is versioned with Pi-hole and is also served by each instance at `http(s)://<host>:<port>/api/docs`. See the [official API overview](https://docs.pi-hole.net/api/), [authentication documentation](https://docs.pi-hole.net/api/auth/), and [FTL query endpoint schema](https://github.com/pi-hole/FTL/blob/master/src/api/docs/content/specs/queries.yaml).

## First check and common symptoms

After setup, wait one poll interval and read **Communication status** and **Ingestion coverage** for the Pi-hole entry. Make a new DNS request from a client that uses that Pi-hole, then wait for the next poll. The client's **DNS queries** total should rise. The initial snapshot establishes the cursor and does not backfill or count the queries already present. A query's class counter can differ from an old Pi-hole counter because this integration counts the first status it observes, and unrecognized or incomplete statuses are provisional.

If the total rises but no Activity entry appears, verify **Activity enabled** is selected, the Activity setting allows that result, and any Activity include filter allows the `pihole_queries` domain. Recorder filters operate on entity states rather than this custom event; if your Recorder configuration excludes the `logbook_entry` event type, allow it there. With **Totals only** or Activity set to `none`, no new Activity entries are sent. If the total does not rise, inspect the entry diagnostics, endpoint and credentials, then check whether the query is still available in Pi-hole's in-memory query history. After an API authentication failure, Home Assistant requests credentials again; use **Reconfigure** for an endpoint change.

## Update or remove

Update through HACS and restart Home Assistant when prompted, or replace `custom_components/pihole_queries` with the new files for a manual installation. Keep the config entry to retain its cursor and counters. Before removal, record that entry's `entry_id` and, if present, its `source_id`; then delete the entry from **Settings → Devices & services**, uninstall it from HACS or delete its folder, and restart Home Assistant. Deleting the entry does not automatically remove its Store files under Home Assistant's `.storage` directory. The Store keeps the cursor and numeric client summaries. There is no integration action to purge this Store. If you also want to erase these files, make a restorable Home Assistant backup, stop Home Assistant, list files whose names start with `pihole_queries_cursor_<source_id>_` for that entry, then remove only those exact files and its legacy `pihole_queries_cursor_<entry_id>` file when present. This also covers earlier replacement generations for that source. Do not remove files belonging to other source IDs or entries. A recreated entry has a new source identity and will not resume the old Store. Previously recorded Activity/Recorder data and external backups follow Home Assistant's retention and backup policies.

## Development and publication

The repository includes HACS validation and Hassfest workflows. Version `0.10.0` adds the `phe-activity-card` Lovelace card and a websocket command that read Recorder statistics of the per-client DNS queries counter to chart activity by block and range and flag sustained unusual activity. Version `0.9.0` retires detailed query history: the integration no longer keeps individual query rows or recent-query attributes, removes those fields from its Store when it loads, and renames the options to **Activity enabled** and **Totals only**. Version `0.8.0` retires the older unversioned allowed/blocked counters while preserving their entity-registry entries and Recorder history, and documents measured high-volume behavior. Version `0.7.0` reduces memory use when publishing query summaries and improves handling of retained client data, with migration and regression coverage. Version `0.6.0` adds rolling one-minute query counters as total-sensor attributes, readable entity names, stable prefixed IDs, and a query-pagination fix. Version `0.5.2` distinguishes API and Logbook errors in the logs for easier troubleshooting. Version `0.5.1` prefixes per-client sensor IDs with `phe` for easier Recorder filtering. Version `0.4.1` allows a 1-second API polling interval. Version `0.4.0` added per-client per-second query-rate sensors, later replaced by rolling one-minute counters in the total sensor's attributes. The repository also includes the API session cleanup fix from `0.3.2`. It is available through this repository as a HACS custom integration; it is not in HACS's default catalog. The request for default-list inclusion at [hacs/default#12355](https://github.com/hacs/default/pull/12355) remains closed while the integration is tested in Home Assistant.

See [CONTRIBUTING.md](../CONTRIBUTING.md) and [SECURITY.md](../SECURITY.md). Spanish installation and lifecycle instructions are in [README.es.md](../README.es.md).

## License

MIT. See [LICENSE](../LICENSE).
