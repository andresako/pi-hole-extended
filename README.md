# Pi-hole Extended

See DNS query activity from **Pi-hole v6** in Home Assistant. The integration creates a device with useful sensors for each Pi-hole client. By default it keeps recent query details and records every new query in Activity/Logbook.

## What you get

- One Home Assistant device per Pi-hole client.
- Four counter sensors per client: total observed queries and allowed/blocked/provisional counts for the active classifier version. Last-minute observation counts are attributes of the total sensor, not separate entities. Existing legacy allowed/blocked entities are disabled by the integration while their registry IDs and Recorder history are retained.
- Individual query entries in **Settings → System → Logs → Activity** by default.
- Simple names and `phe_` IDs for per-client sensors; diagnostic IDs are scoped to the integration entry.

## Upgrade notes for 0.8.0

The two older unversioned allowed/blocked client counters are retired. Home Assistant disables their existing entity-registry entries while preserving their unique IDs, custom entity IDs, custom names, and Recorder history. Automations or dashboards that use those entities should be updated to the versioned allowed/blocked counters. New installations create only the current four client counters.

In a synthetic test with 100 clients and 100 queries per second, detailed mode processed a 10-minute feed of 60,000 rows with the final cursor about 0.12 seconds after the feed ended. Aggregate mode took about 360 seconds to drain after the same feed ended (about 960 seconds total). Separately, recovering a 60,000-row backlog took about 0.65 seconds in detailed mode and 13.88 seconds in aggregate mode (13.98 seconds maximum across five runs). These local synthetic results are not a throughput guarantee for other hardware or Pi-hole installations. They show that aggregate mode can lag under sustained high-volume input, even though it uses less detail and avoids new Activity entries.

## Install with HACS

1. In HACS, open **Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/Andresako/pi-hole-extended` and choose **Integration**.
3. Install **Pi-hole Extended**, then restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration**, search for **Pi-hole Extended**, and enter your Pi-hole details.

The integration is installed as a custom HACS repository; it is not currently in HACS's default catalog.

## Set up

The integration requires Home Assistant 2025.1 or newer and Pi-hole v6. Its query contract is checked against FTL 6.1.0–6.8.x; each Pi-hole also serves its own API reference at `/api/docs`. Create an application password from Pi-hole's web interface Settings page; Pi-hole shows it only once. During setup, enter:

- Pi-hole hostname or IP address (without `http://`, `https://`, a port, or a path) and its web/API port.
- Whether Pi-hole uses HTTPS and whether to verify its certificate.
- The application password.
- How often Home Assistant checks for new queries (default: 15 seconds).

The port is used exactly as entered: HTTPS does not silently change it to 443, so keep a custom port when switching protocol. The password is stored in the Home Assistant config entry. It is not included in this repository or integration logs. Start with a 5-second interval; shorter intervals show new queries sooner and make more API requests. See Pi-hole's [official authentication guide](https://docs.pi-hole.net/api/auth/) for session-based API access.

After setup, wait for at least one polling interval and check the per-entry **Communication status** and **Ingestion coverage** diagnostic sensors. The first read establishes a baseline and does not count or log the queries already in Pi-hole's current snapshot. To check new activity, make a fresh DNS request from a client that uses this Pi-hole, then wait for the next poll. The client's **DNS queries** total should increase; the query appears in Activity only when detailed retention is selected and the Activity option allows it. A blocked query increments a blocked counter; a permitted query increments an allowed counter; an unrecognized or incomplete status remains provisional. The total counts observations made by Home Assistant, not every query Pi-hole handled.

If the total increases but Activity has no entry, check that query retention is **Detailed**, Activity events are set to **All** or **Blocked** for the result, and any Activity include filter allows the `pihole_queries` domain. Recorder filters operate on entity states rather than this custom event; if your Recorder configuration excludes the `logbook_entry` event type, allow it there. Activity/Recorder persistence is controlled by Home Assistant and is not verified by this integration. If the total does not increase, check the diagnostic status, endpoint, credentials, poll interval, and whether the query is new to Pi-hole's in-memory history.

Use the integration's **Reconfigure** action to change the endpoint. Confirm **Same Pi-hole** to preserve its source identity, cursor, and existing entities, including when the protocol or port changes. Choose **Replacement Pi-hole** only when the server was replaced: it starts a new generation and baseline, and disables the previous generation's client sensors while retaining their Recorder history. You can leave the optional password blank to keep the saved password.

## Query retention

In the integration options, choose **Detailed query history** or **Aggregate totals only**. Detailed history is the default and keeps recent query rows for sensors. Aggregate mode retains client identity and numeric totals in the integration Store, with no query domains, IDs, raw statuses, or individual query rows; it also disables new Activity entries. The separate Activity option can record all events (the default), blocked events only, or none while detailed history is enabled. These choices do not change the counters.

Switching to aggregate mode removes query details from the integration's current Store and legacy backup. It does not erase earlier Recorder or Activity history, exports, or backups; Home Assistant may continue to show data that was already recorded.

## Update or remove

For a HACS installation, update **Pi-hole Extended** in HACS and restart Home Assistant when prompted. For a manual installation, replace `custom_components/pihole_queries` with the new files and restart. Keep the integration entry to retain its cursor and counters. To remove it, first record that entry's `entry_id` and, if present, its `source_id`; then delete the entry from **Settings → Devices & services**, uninstall it from HACS or delete its integration folder, and restart Home Assistant. Removing the entry does not automatically delete the integration's Store files under Home Assistant's `.storage` directory. Detailed mode can store the last query and up to ten recent rows per client; aggregate mode keeps the cursor and numeric summaries. The integration has no Store purge action. If you also want to erase those files, make a restorable Home Assistant backup, stop Home Assistant, list files whose names start with `pihole_queries_cursor_<source_id>_` for that entry, then remove only those exact files and its legacy `pihole_queries_cursor_<entry_id>` file when present. This also covers earlier replacement generations for that source. Do not remove files belonging to other source IDs or entries, and do not delete the `.storage` directory. A recreated entry has a new source identity and will not resume the old Store. Previously recorded Activity/Recorder data and external backups follow Home Assistant's retention and backup policies.

## More information

- [Guía en español](README.es.md)
- [Technical details and limitations](docs/DETAILS.md)
- [Detalles técnicos en español](docs/DETALLES_ES.md)
- [Report a problem](https://github.com/Andresako/pi-hole-extended/issues)
