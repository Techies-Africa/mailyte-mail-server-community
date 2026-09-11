---
title: Guides
description: Practical walkthroughs for common tasks — migration, monitoring, scaling, troubleshooting, and more.
---

# Guides

Step-by-step guides that walk you through real tasks. Each one assumes you already have a running Mailyte server and takes you from start to finish with concrete commands and examples.

!!! prerequisite "Before you begin"
    These guides assume a working Mailyte deployment. If you don't have one yet, start with [Installation](../getting-started/installation.md) first.

---

## At a Glance

```mermaid
flowchart LR
    subgraph Migrate["Migration"]
        MG[Mailgun]
        SG[SendGrid]
    end

    subgraph Configure["Email Config"]
        DKIM[DKIM Setup]
        DELIV[Deliverability]
        DOM[Domains]
        ORG[Organizations]
    end

    subgraph Infra["Infrastructure"]
        SCALE[Scaling]
        MON[Monitoring]
        BACK[Backups]
        INT[Integrations]
    end

    subgraph Fix["Troubleshooting"]
        EMAIL[Delivery Issues]
        SSL[SSL Issues]
        DB[Database]
        NET[Network]
    end

    Migrate --> Configure --> Infra
    Fix -.->|"when things break"| Infra
```

## Migration

Moving from another email provider? These guides cover the full process — exporting data, importing via the API, updating DNS, and verifying delivery.

<div class="grid cards" markdown>

-   :material-email-sync:{ .lg .middle } **Migrating from Mailgun**

    ---

    Export domains and mailboxes, import via API, cut over DNS, and verify delivery.

    [:octicons-arrow-right-24: Mailgun Migration](migrating-from-mailgun.md)

-   :material-email-sync-outline:{ .lg .middle } **Migrating from SendGrid**

    ---

    Export sending domains and suppression lists, switch sending to SMTP credentials, and validate SPF/DKIM.

    [:octicons-arrow-right-24: SendGrid Migration](migrating-from-sendgrid.md)

</div>

## Email Configuration

| Guide | What It Covers | Difficulty |
|-------|---------------|------------|
| [Setting Up DKIM](setting-up-dkim.md) | Generate keys, add DNS records, configure Rspamd signing | Beginner |
| [Improving Deliverability](improving-deliverability.md) | SPF, DKIM, DMARC alignment, IP warm-up, reputation management | Intermediate |
| [Domain Configuration](domain-configuration.md) | Adding domains, DNS verification, aliases, per-domain settings | Beginner |
| [Managing Organizations](organization-management.md) | Bulk operations, quota management, billing integration via external IDs | Intermediate |

!!! tip "New to email infrastructure?"
    Start with [Domain Configuration](domain-configuration.md), then [Setting Up DKIM](setting-up-dkim.md), then [Improving Deliverability](improving-deliverability.md). This order builds on each previous step.

## Marketing & Bulk Sending

Relay marketing and bulk campaigns through Mailyte using the campaign tool you already have. Mailyte is the pipe, suppression, unsubscribe compliance, and billing — bring your own contacts and scheduling.

| Guide | What It Covers | Difficulty |
|-------|---------------|------------|
| [Send Marketing & Bulk Email](sending-marketing-email.md) | What SMTP Send is, sending requirements, bounce/complaint limits, and warm-up | Beginner |
| [Connect Sendy](connect-sendy.md) | Point Sendy's SMTP sending server at Mailyte | Beginner |
| [Connect MailWizz](connect-mailwizz.md) | Add Mailyte as an SMTP delivery server in MailWizz | Beginner |
| [Connect WordPress](connect-wordpress.md) | Route WP Mail SMTP and MailPoet through Mailyte | Beginner |

!!! tip "Read the requirements first"
    Marketing senders must clear the gates in [Send Marketing & Bulk Email](sending-marketing-email.md) — verified domain, accepted AUP, opt-in lists, prepaid credits — before a live sending credential is issued.

## Infrastructure

| Guide | What It Covers | Difficulty |
|-------|---------------|------------|
| [Scaling to Millions](scaling-to-millions.md) | Postfix/MySQL/Redis tuning, worker replicas, and what's architecture work | Advanced |
| [Monitoring Setup](monitoring-setup.md) | The built-in Prometheus/Grafana/Alertmanager stack and how to reach it | Intermediate |
| [Prometheus Configuration](prometheus-configuration.md) | The shipped scrape jobs and alert rules, and how to extend them | Intermediate |
| [Grafana Dashboards](grafana-setup.md) | Provisioned dashboards, custom panels, and Grafana alerting | Intermediate |
| [Backup Automation](backup-automation.md) | backup.sh, systemd timers, age encryption, S3 offsite, restore drills | Intermediate |
| [Custom Integrations](custom-integrations.md) | Webhooks for CRM/ticketing systems, API automation patterns | Intermediate |

## Troubleshooting

Something broken? Find your symptom below and follow the guide.

<div class="grid cards" markdown>

-   :material-email-off:{ .lg .middle } **Email Delivery Issues**

    ---

    Email not arriving, stuck in queue, bouncing, or being rejected by remote servers.

    [:octicons-arrow-right-24: Diagnose delivery issues](troubleshooting/email-delivery-issues.md)

-   :material-lock-alert:{ .lg .middle } **SSL Certificate Issues**

    ---

    Certificates not renewing, domain mismatch errors, Let's Encrypt rate limits.

    [:octicons-arrow-right-24: Fix SSL issues](troubleshooting/ssl-certificate-issues.md)

-   :material-database-alert:{ .lg .middle } **Database Performance**

    ---

    Slow queries, connection pool exhaustion, table locks, replication lag.

    [:octicons-arrow-right-24: Fix database issues](troubleshooting/database-performance.md)

-   :material-lan-disconnect:{ .lg .middle } **Network Connectivity**

    ---

    Ports blocked by firewall, DNS resolution failures, Docker networking problems.

    [:octicons-arrow-right-24: Fix network issues](troubleshooting/network-connectivity.md)

-   :material-chart-bell-curve:{ .lg .middle } **Monitoring Issues**

    ---

    Prometheus not scraping, Grafana dashboard errors, missing metrics.

    [:octicons-arrow-right-24: Fix monitoring issues](troubleshooting/monitoring-issues.md)

-   :material-server-off:{ .lg .middle } **Service Failures**

    ---

    Container crashes, OOM kills, disk full, services not starting.

    [:octicons-arrow-right-24: Fix service failures](troubleshooting/service-failures.md)

</div>

## Related Sections

- **[Configuration](../configuration/index.md)** — Detailed configuration reference for all components
- **[Deployment](../deployment/index.md)** — Production setup, scaling, and disaster recovery
- **[Monitoring](../monitoring/index.md)** — Dashboards, alerts, and health checks
- **[Reference](../reference/index.md)** — CLI commands, error codes, and database schema
