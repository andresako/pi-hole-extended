# Pi-hole Extended

**See the DNS activity Pi-hole v6 reports to Home Assistant, organized by client.** Pi-hole Extended provides per-client query counters, optional recent query details, and diagnostics for the integration's connection and query coverage.

> A community-built Home Assistant custom integration. It is not affiliated with or endorsed by Home Assistant or Pi-hole.

[![Latest release](https://img.shields.io/github/v/release/Andresako/pi-hole-extended?label=release)](https://github.com/Andresako/pi-hole-extended/releases/latest) [![HACS validation](https://github.com/andresako/pi-hole-extended/actions/workflows/hacs.yaml/badge.svg)](https://github.com/andresako/pi-hole-extended/actions/workflows/hacs.yaml) [![Hassfest](https://github.com/andresako/pi-hole-extended/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/andresako/pi-hole-extended/actions/workflows/hassfest.yaml)

## At a glance

- One Home Assistant device for each client found in Pi-hole's query history.
- Five sensors per client: total observed queries, queries in Pi-hole's last-minute window, allowed queries, blocked queries, and provisional queries.
- Four integration diagnostics: communication status, last successful read, last observed query, and ingestion coverage.
- Choose **Detailed query history** for recent query details and optional Home Assistant Activity entries, or **Aggregate totals only** to keep client identities and numeric totals without query rows or domains.
- A starter dashboard with four native Home Assistant views: **Summary**, **Clients**, **Details**, and **Diagnostics**.

The integration counts new queries it observes while polling Pi-hole. It does not recreate all traffic Pi-hole has ever handled. Entity totals are 29 for 5 clients, 129 for 25 clients, and 504 for 100 clients (five per client plus four integration diagnostics).

## Dashboard previews

The screenshots below show a customized four-view Home Assistant example. Its 24-hour chart uses synthetic observations imported into a disposable Recorder for demonstration; Pi-hole Extended begins counting after setup and does not restore historical Pi-hole queries.

![Pi-hole Extended overview with synthetic client totals and a 24-hour query chart](docs/images/overview.png)

*Demonstration data — simulated Home Assistant observations. Client totals and chart history are synthetic and demonstrate the dashboard layout.*

![Pi-hole Extended diagnostics with synthetic query history and integration health entities](docs/images/diagnostics.png)

*Demonstration data — simulated Home Assistant observations. The chart is example Recorder history; connection and ingestion values reflect the isolated demo instance.*

## Requirements

- Home Assistant **2025.1 or newer**.
- Pi-hole **v6**. The query API contract is checked against FTL **6.1.0–6.8.x**. Pi-hole also provides its API reference at `/api/docs`.

## Install with HACS

Pi-hole Extended is available by adding its repository as a custom HACS repository.

1. In Home Assistant, open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/Andresako/pi-hole-extended` and select **Integration**.
3. Install **Pi-hole Extended**, then restart Home Assistant.
4. Open **Settings → Devices & services → Add integration**, search for **Pi-hole Extended**, and follow the setup form.

For a manual installation, download [`pihole_queries-0.8.1.zip`](https://github.com/Andresako/pi-hole-extended/releases/download/0.8.1/pihole_queries-0.8.1.zip), extract `custom_components/pihole_queries` into your Home Assistant configuration directory, and restart Home Assistant.

## Configure Pi-hole

Create an application password in Pi-hole's web interface under **Settings**. Pi-hole displays the password only once; see the [Pi-hole authentication guide](https://docs.pi-hole.net/api/auth/).

During setup, provide:

- Pi-hole hostname or IP address, without protocol, port, or path.
- Web/API port.
- Whether to use HTTPS and whether to verify the TLS certificate.
- The application password.
- Poll interval, from **1 to 3600 seconds** (default: **15 seconds**).

The password is stored with the Home Assistant integration entry and is not included in integration logs. A shorter poll interval can show new observations sooner and makes more API requests.

## See your first observations

After setup, wait for a successful poll and check **Communication status** and **Ingestion coverage** in the integration's diagnostic entities. The first read establishes a baseline and does not count queries already present in Pi-hole's current snapshot. Then make a fresh DNS request from a device that uses this Pi-hole and wait for the next poll.

The client's **DNS queries** counter increases when a new query is observed. Allowed and blocked queries update their classifier counters; unrecognized or incomplete results remain provisional and are excluded from the classified percentage. In Detailed mode, a new query can also appear in Home Assistant's **Activity** when the Activity option permits it. Activity and Recorder persistence depend on your Home Assistant configuration.

## Choose what to retain

In the integration's options, choose a retention mode:

- **Detailed query history** (default): retains recent query details for client sensors. Choose whether Activity records **All events**, **Blocked events only**, or **No Activity events**.
- **Aggregate totals only**: retains client identities and numeric totals without query domains, IDs, raw statuses, or individual query rows. This mode does not create new Activity entries.

See [technical details and limits](docs/DETAILS.md) for retention behavior and high-volume considerations.

## Add the example dashboard

The [starter dashboard YAML](examples/dashboards/pihole-extended.yaml) contains four views built from Home Assistant's native cards. It uses placeholder entity IDs: copy the YAML into a new dashboard and map each placeholder to the entity IDs in your own entity registry. Follow the [dashboard guide](docs/dashboard-examples.md); do not replace an existing dashboard's configuration.

## Troubleshooting

- **Counters do not increase:** check communication status, host, port, TLS settings, password, poll interval, and whether Pi-hole still exposes the query in its in-memory history.
- **Counters increase but Activity is empty:** confirm Detailed mode is selected, Activity is set to All or Blocked only as desired, and Home Assistant's Activity/Recorder filters allow the event. The integration does not verify persistence.
- **Aggregate mode falls behind during sustained heavy traffic:** aggregate processing can lag when many observations arrive continuously. See the synthetic measurements and their limits in [technical details](docs/DETAILS.md).

## Links

- [Dashboard guide](docs/dashboard-examples.md) · [Technical details and limitations](docs/DETAILS.md)
- [Report a problem](https://github.com/Andresako/pi-hole-extended/issues) · [Security policy](SECURITY.md) · [MIT license](LICENSE)
- [Read this guide in Spanish](README.es.md)
