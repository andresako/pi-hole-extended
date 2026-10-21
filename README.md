# Pi-hole Extended

See individual DNS queries from **Pi-hole v6** in Home Assistant. The integration records new queries in Activity/Logbook and creates a device with useful sensors for each Pi-hole client.

## What you get

- One Home Assistant device per Pi-hole client.
- Six sensors per client: total, allowed, and blocked queries, plus the same counts for the last 60 seconds.
- Individual query entries in **Settings → System → Logs → Activity**.
- Simple entity names and `phe_` entity IDs for easy filtering in Recorder.

## Install with HACS

1. In HACS, open **Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/Andresako/pi-hole-extended` and choose **Integration**.
3. Install **Pi-hole Extended**, then restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration**, search for **Pi-hole Extended**, and enter your Pi-hole details.

The integration is installed as a custom HACS repository; it is not currently in HACS's default catalog.

## Set up

You need Pi-hole v6 and an application password created in Pi-hole. During setup, enter:

- Pi-hole host and web/API port.
- Whether Pi-hole uses HTTPS and whether to verify its certificate.
- The application password.
- How often Home Assistant checks for new queries (default: 15 seconds).

The password is stored in the Home Assistant config entry. It is not included in this repository or integration logs. Start with a 5-second interval; shorter intervals show new queries sooner and make more API requests.

## More information

- [Guía en español](README.es.md)
- [Technical details and limitations](docs/DETAILS.md)
- [Detalles técnicos en español](docs/DETALLES_ES.md)
- [Report a problem](https://github.com/Andresako/pi-hole-extended/issues)
