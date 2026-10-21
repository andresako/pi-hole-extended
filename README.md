# Pi-hole Extended for Home Assistant

A standalone custom integration for Pi-hole v6. It polls Pi-hole's official `GET /api/queries` endpoint, records newly observed DNS queries in Home Assistant Activity, and creates one device per client.

Documentation: [English](README.md) · [Español](README.es.md)

## Features

- Configure host, port, HTTPS, Pi-hole application password, and polling interval in Home Assistant.
- Record the query time, client name/IP, domain, result/status, query type, reply, and other fields when Pi-hole provides them.
- Resume using Pi-hole's query IDs and API cursor, avoiding normal duplicate entries across polling cycles and Home Assistant restarts.
- Create a per-client device with cumulative total/allowed/blocked counters and total/allowed/blocked queries-per-second sensors.
- Keep the latest query and up to ten recent query details in the total sensor's attributes.
- Keep DNS history in Home Assistant Recorder and view it in Activity/Logbook.
- Operate independently of Home Assistant's built-in Pi-hole integration.

## Installation

### HACS

The repository is [andresako/pi-hole-extended](https://github.com/andresako/pi-hole-extended). It can be added as a custom repository through **HACS → Integrations → ⋮ → Custom repositories**, category **Integration**. Inclusion in HACS's default catalog is paused until the integration has been verified; the earlier request was closed.

### Manual

Copy `custom_components/pihole_queries` into the `custom_components` directory under your Home Assistant configuration directory, restart Home Assistant, then use **Settings → Devices & services → Add integration** and select **Pi-hole Extended**.

## Configuration

The config flow requests:

- Hostname or IP address and web/API port.
- Whether to use HTTPS.
- Whether to verify the HTTPS certificate. Keep this enabled for trusted certificates; disable it only for a self-signed certificate.
- A Pi-hole application password.
- Poll interval from 1 to 3600 seconds (default: 15 seconds).

Create an application password in Pi-hole and enter it in Home Assistant. It is stored in the config entry and is not part of source files, examples, or integration logs. Pi-hole v6 uses a temporary SID; the integration obtains one through `POST /api/auth` and renews it after an expired-session response.

At a 1-second interval, normal operation makes about 60 authenticated `GET /api/queries` requests per minute, reusing one SID; it does not log in for each poll. Catching up after a backlog can require additional paginated requests. Pi-hole documents login-attempt throttling for `POST /api/auth`, but does not publish a numeric limit for normal authenticated query reads. No supported request threshold is documented, so 1 second is an optional high-frequency setting, not a guaranteed safe maximum. Start at 5 seconds if Pi-hole is a low-power device and watch its load. Separately, Pi-hole FTL versions before 6.7 have an upstream web/API denial-of-service issue involving uncontrolled request floods; update FTL to 6.7 or later before exposing its web/API service to untrusted networks.

## Query history and limits

The integration calls Home Assistant's documented `logbook.log` action and tags each entry with the `pihole_queries` domain. Entries appear in Activity/Logbook, not on the integration's configuration page. The original Pi-hole timestamp is included in each message; the Activity row itself is timestamped when Home Assistant records it, typically within one polling interval. If Activity uses an include filter, include the `pihole_queries` domain. Recorder retention and filters determine how long the entries remain. A high DNS query rate can produce substantial Recorder and Logbook data.

Each discovered Pi-hole client gets a device with six sensors: cumulative total, allowed, and blocked counts, plus total, allowed, and blocked queries per second. Counts include queries observed since the integration was first set up. Rates are calculated between successful polling cycles; after startup they begin at zero and update on the next poll. When no new queries arrive, the corresponding rate is zero. The total sensor keeps client name/IP, the latest query, and up to ten recent queries in its attributes. Existing client devices remain available if a client is removed from Pi-hole; a new client appears after its first query is detected. The initial snapshot discovers clients but does not count or log its existing queries.

Sensor entity IDs start with `sensor.phe_`, for example `sensor.phe_192_168_68_94_consultas_dns`. This prefix makes them easy to find and filter in Recorder with `entity_globs`. If you already use a `recorder.include` block, you can add:

```yaml
recorder:
  include:
    entity_globs:
      - sensor.phe_*
```

Keep in mind that `recorder.include` makes Recorder save only included entities. Version 0.5.1 migrates default-generated IDs and preserves IDs that were already customized. Update any dashboard or automation references that used an old ID after the migration.

The first setup stores a baseline ID and does not backfill older Pi-hole queries. The integration reads the in-memory query history exposed by Pi-hole. Queries no longer available there cannot be recovered after a long outage or an FTL history reset. Pagination fetches backlogs in 1,000-row pages. A very large backlog can extend a polling cycle. If Home Assistant stops after writing a Logbook entry but before persisting its cursor, the last entry may be replayed on restart because Recorder and integration storage do not share an atomic transaction.

The API schema is versioned with Pi-hole and is also served by each instance at `http(s)://<host>:<port>/api/docs`. See the [official API overview](https://docs.pi-hole.net/api/), [authentication documentation](https://docs.pi-hole.net/api/auth/), and [FTL query endpoint schema](https://github.com/pi-hole/FTL/blob/master/src/api/docs/content/specs/queries.yaml).

## Development and publication

The repository includes HACS validation and Hassfest workflows. Version `0.5.2` distinguishes API and Logbook errors in the logs for easier troubleshooting. Version `0.5.1` prefixes sensor IDs with `phe` for easier Recorder filtering. Version `0.4.1` allows a 1-second polling interval; `0.4.0` added per-client query-rate sensors and removed the last-domain and last-result sensors. The repository also includes the API session cleanup fix from `0.3.2`. It is available through this repository as a HACS custom integration; it is not in HACS's default catalog. The request for default-list inclusion at [hacs/default#12355](https://github.com/hacs/default/pull/12355) remains closed while the integration is tested in Home Assistant.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). Spanish installation and lifecycle instructions are in [README.es.md](README.es.md).

## License

MIT. See [LICENSE](LICENSE).
