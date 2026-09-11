---
title: Reference
description: Quick-lookup reference material — CLI commands, database schema, error codes, metrics, and a glossary of email terminology.
---

# Reference

This section is not meant to be read front to back. It's a reference — look up what you need, find the answer, get back to work.

---

## Quick Navigation

<div class="grid cards" markdown>

-   :material-console:{ .lg .middle } **CLI Commands**

    ---

    The management CLIs (`start.sh`, `manage.py`, `scripts/`) plus the docker exec commands for Postfix, Dovecot, Rspamd, and MySQL.

    [:octicons-arrow-right-24: CLI Commands](cli-commands.md)

-   :material-cog:{ .lg .middle } **Configuration Reference**

    ---

    Where every config file actually lives — baked image configs, host mounts, and generated files — with the key effective settings.

    [:octicons-arrow-right-24: Configuration Reference](configuration-reference.md)

-   :material-database:{ .lg .middle } **Database Schema**

    ---

    Tables, columns, types, relationships, and indexes for the MySQL database.

    [:octicons-arrow-right-24: Database Schema](database-schema.md)

-   :material-api:{ .lg .middle } **API Endpoints**

    ---

    Quick table of all REST API endpoints with methods, parameters, and response formats.

    [:octicons-arrow-right-24: API Endpoints](api-endpoints.md)

</div>

## Full Reference Index

| Reference | What's In It | When You Need It |
|-----------|-------------|------------------|
| [CLI Commands](cli-commands.md) | Management CLIs, scripts/, and docker exec commands | Managing services from the command line |
| [Configuration Reference](configuration-reference.md) | Config file locations and key effective settings | Customizing service behavior |
| [Database Schema](database-schema.md) | All 83 tables at the current Alembic head | Writing queries or building integrations |
| [Log Formats](log-formats.md) | Where logs live, their formats, and the log ingestor | Debugging delivery or auth issues |
| [Webhook Events](webhook-events.md) | The event catalogue, payloads, signing, retries | Building webhook consumers |
| [Error Codes](error-codes.md) | API error envelope + codes, SMTP/DSN, Rspamd actions | Troubleshooting failures |
| [Performance Metrics](performance-metrics.md) | Key metrics and their healthy ranges | Capacity planning and alerting |
| [Prometheus Metrics](prometheus-metrics.md) | Every exposed metric, with the service-prefix rule | Writing PromQL queries and dashboards |
| [API Endpoints](api-endpoints.md) | All REST endpoints in one table | Quick API lookup |
| [Environment Variables](environment-variables.md) | The variables code actually reads, with defaults | Initial setup and reconfiguration |
| [Glossary](glossary.md) | Email jargon explained in plain English | Understanding the docs |

!!! tip "Most common lookups"
    - **"What's this error code?"** — [Error Codes](error-codes.md)
    - **"How do I check the mail queue?"** — [CLI Commands](cli-commands.md)
    - **"What env vars do I need?"** — [Environment Variables](environment-variables.md)
    - **"What does this webhook payload look like?"** — [Webhook Events](webhook-events.md)
    - **"What table stores mailboxes?"** — [Database Schema](database-schema.md)

## Related Sections

- **[Configuration](../configuration/index.md)** — Detailed setup guides for each component
- **[Guides > Troubleshooting](../guides/index.md#troubleshooting)** — Step-by-step debugging walkthroughs
- **[Worker > API Gateway](../worker/api.md)** — Full API documentation
