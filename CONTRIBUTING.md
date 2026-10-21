# Contributing

Contributions are welcome. Please open an issue before substantial changes so
the scope and compatibility can be discussed.

For pull requests:

- Keep the integration independent of Home Assistant's built-in Pi-hole integration.
- Use only documented Pi-hole v6 API behavior and supported Home Assistant APIs.
- Never add credentials, session IDs, or private query data to source, fixtures,
  screenshots, or logs.
- Update the README when configuration, supported API fields, or limitations change.
- Include the Home Assistant and Pi-hole versions used for manual verification.

Before a release, run both repository checks: HACS validation and Hassfest.
