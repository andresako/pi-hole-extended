# Pi-hole DNS Queries for Home Assistant

A standalone custom integration for Pi-hole v6. It polls Pi-hole's official `GET /api/queries` endpoint and adds each newly observed DNS query to Home Assistant Activity (Logbook), including allowed and blocked queries.

Documentation: [English](README.md) · [Español](README.es.md)

## Features

- Configure host, port, HTTPS, Pi-hole application password, and polling interval in Home Assistant.
- Record the query time, client name/IP, domain, result/status, query type, reply, and other fields when Pi-hole provides them.
- Resume using Pi-hole's query IDs and API cursor, avoiding normal duplicate entries across polling cycles and Home Assistant restarts.
- Keep DNS history in Home Assistant Recorder and view it in Activity/Logbook.
- Operate independently of Home Assistant's built-in Pi-hole integration.

## Installation

### HACS

This project is being prepared for public HACS distribution. Once published, add its GitHub repository in **HACS → Integrations → ⋮ → Custom repositories** with category **Integration**, install **Pi-hole DNS Queries**, and restart Home Assistant. Inclusion in the HACS default list requires a separate review and can take months.

### Manual

Copy `custom_components/pihole_queries` into the `custom_components` directory under your Home Assistant configuration directory, restart Home Assistant, then use **Settings → Devices & services → Add integration** and select **Pi-hole DNS Queries**.

## Configuration

The config flow requests:

- Hostname or IP address and web/API port.
- Whether to use HTTPS.
- A Pi-hole application password.
- Poll interval from 5 to 3600 seconds (default: 15 seconds).

Create an application password in Pi-hole and enter it in Home Assistant. It is stored in the config entry and is not part of source files, examples, or integration logs. Pi-hole v6 uses a temporary SID; the integration obtains one through `POST /api/auth` and renews it after an expired-session response.

## Query history and limits

The integration calls Home Assistant's documented `logbook.log` action. The original Pi-hole timestamp is included in each message; the Activity row itself is timestamped when Home Assistant records it, typically within one polling interval. Recorder retention and filters determine how long the entries remain. A high DNS query rate can produce substantial Recorder and Logbook data.

The first setup stores a baseline ID and does not backfill older Pi-hole queries. The integration reads the in-memory query history exposed by Pi-hole. Queries no longer available there cannot be recovered after a long outage or an FTL history reset. Pagination fetches backlogs in 1,000-row pages. A very large backlog can extend a polling cycle. If Home Assistant stops after writing a Logbook entry but before persisting its cursor, the last entry may be replayed on restart because Recorder and integration storage do not share an atomic transaction.

The API schema is versioned with Pi-hole and is also served by each instance at `http(s)://<host>:<port>/api/docs`. See the [official API overview](https://docs.pi-hole.net/api/), [authentication documentation](https://docs.pi-hole.net/api/auth/), and [FTL query endpoint schema](https://github.com/pi-hole/FTL/blob/master/src/api/docs/content/specs/queries.yaml).

## Development and publication

The repository includes HACS validation and Hassfest workflows. For HACS default-list submission, the maintainer must set the repository description and topics, pass both workflows, publish a GitHub release, and open an editable PR to `hacs/default`. HACS default inclusion is a separate review; the repository can first be installed as a custom repository.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). Spanish installation and lifecycle instructions are in [README.es.md](README.es.md).

## License

MIT. See [LICENSE](LICENSE).
