---
hide:
  - navigation
  - toc
---

# Mailyte Email Server Handbook

<div style="font-size: 1.15rem; color: var(--md-default-fg-color--light); margin-bottom: 2rem; max-width: 48rem;">
The complete developer guide to the Mailyte Email Server — the infrastructure behind Mailyte's email hosting platform. Everything from first setup to scaling to millions of emails, explained in plain language.
</div>

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } **Getting Started**

    ---

    Install the server, configure your environment, and send your first email in under 15 minutes.

    [:octicons-arrow-right-24: Jump in](getting-started/index.md)

-   :material-sitemap:{ .lg .middle } **Architecture**

    ---

    How all the pieces fit together — services, data flows, database design, and multi-tenant isolation.

    [:octicons-arrow-right-24: See the big picture](architecture/index.md)

-   :material-api:{ .lg .middle } **API Reference**

    ---

    Every REST endpoint documented with parameters, response schemas, and copy-paste examples in 4 languages.

    [:octicons-arrow-right-24: Explore the API](api/index.md)

-   :material-cog:{ .lg .middle } **Configuration**

    ---

    Fine-tune Postfix, Dovecot, Rspamd, SSL certificates, DNS records, and performance knobs.

    [:octicons-arrow-right-24: Configure it](configuration/index.md)

-   :material-shield-check:{ .lg .middle } **Security**

    ---

    Authentication, encryption at every layer, intrusion detection, and a pre-launch hardening checklist.

    [:octicons-arrow-right-24: Lock it down](security/index.md)

-   :material-wrench:{ .lg .middle } **Development**

    ---

    Set up your dev environment, build custom workers, write tests, and ship releases.

    [:octicons-arrow-right-24: Start building](development/index.md)

</div>

---

## What is Mailyte Email Server?

It's a complete, production-ready email system that runs inside Docker containers. It handles the entire lifecycle of an email — from the moment someone clicks "Send" to delivery, spam filtering, tracking, and storage.

Here's what you get out of the box:

| Capability | What it does | Powered by |
|-----------|-------------|------------|
| **Send & receive email** | SMTP inbound/outbound with virtual domains | Postfix |
| **Email client access** | IMAP and POP3 for Thunderbird, Outlook, mobile apps | Dovecot |
| **Spam filtering** | Machine-learning detection, antivirus, greylisting | Rspamd + ClamAV |
| **REST API** | Manage everything programmatically — orgs, domains, mailboxes | FastAPI (Python) |
| **Email tracking** | Open pixels, click-through link rewrites, engagement analytics | Tracking worker |
| **AI-powered search** | Semantic email search using vector embeddings | Qdrant + RAG worker |
| **Real-time webhooks** | Notify your app when emails are sent, delivered, bounced, opened | Webhook worker |
| **Auto SSL certificates** | Free HTTPS/TLS via Let's Encrypt with auto-renewal | Cert manager |
| **Monitoring** | Prometheus metrics, Grafana dashboards, health checks | Monitoring stack |
| **Multi-tenant** | Full data isolation per organization with quota management | Built into every layer |
| **Cloud backup** | Automated backups to AWS S3 or Azure Blob Storage | Cloud sync worker |
| **Rate limiting** | Per-sender, per-domain, and per-org abuse prevention | Rate limiter + Redis |

---

## The Architecture at a Glance

The server is organized in three layers, all running as Docker containers:

```mermaid
graph TB
    subgraph "<b>API Layer</b>"
        API["REST API<br/><small>:5000 &bull; FastAPI</small>"]
    end

    subgraph "<b>Mail Infrastructure</b>"
        POSTFIX["Postfix<br/><small>:25 :587 :465</small>"]
        DOVECOT["Dovecot<br/><small>:143 :993</small>"]
        RSPAMD["Rspamd<br/><small>:11332</small>"]
    end

    subgraph "<b>Background Workers</b>"
        TRACKING["Tracking"]
        WEBHOOKS["Webhooks"]
        RATELIMIT["Rate Limiter"]
        ANALYTICS["Analytics"]
        RAG["AI Search"]
        QUEUE["Queue Mgr"]
        STORAGE["Storage"]
        BACKUP["Backup"]
    end

    subgraph "<b>Data Stores</b>"
        MYSQL[("MySQL")]
        REDIS[("Redis")]
        QDRANT[("Qdrant")]
        FS["Filesystem"]
    end

    API --> POSTFIX & DOVECOT
    POSTFIX --> RSPAMD
    POSTFIX --> TRACKING & WEBHOOKS & RATELIMIT

    TRACKING --> MYSQL
    WEBHOOKS --> REDIS
    RATELIMIT --> REDIS
    ANALYTICS --> MYSQL
    RAG --> QDRANT
    QUEUE --> MYSQL
    STORAGE --> FS
    BACKUP --> FS
```

!!! tip "The 30-second version"
    **Postfix** sends and receives email. **Dovecot** lets email clients read it. **Rspamd** blocks spam. The **API** manages everything. **Workers** handle tracking, webhooks, analytics, and more. **MySQL** stores the data, **Redis** caches it, and **Qdrant** powers AI search.

---

## System Requirements

| Resource | Minimum | Recommended | High Volume |
|----------|---------|-------------|-------------|
| **CPU** | 2 cores | 4 cores | 8+ cores |
| **RAM** | 4 GB | 8 GB | 16+ GB |
| **Disk** | 50 GB SSD | 200 GB SSD | 1 TB+ NVMe |
| **Network** | 100 Mbps | 1 Gbps | 10+ Gbps |
| **Docker** | 24.0+ | Latest | Latest |
| **Docker Compose** | v2.20+ | Latest | Latest |

---

## Where to Start

=== "I need to set this up"

    Head to the [Installation Guide](getting-started/installation.md) for a step-by-step walkthrough. You'll have a working server in about 15 minutes.

=== "I need to understand how it works"

    Start with the [Architecture Overview](architecture/index.md) to see the big picture, then dive into [How Data Flows](architecture/data-flow.md) to follow an email through the system.

=== "I need to call the API"

    The [API Reference](api/index.md) has every endpoint documented. Check the [Code Examples](api/examples/curl.md) for ready-to-use snippets in curl, Python, JavaScript, and PHP.

=== "I need to add a feature"

    The [Development Guide](development/index.md) covers your dev environment setup, and [Building Custom Workers](development/custom-workers.md) shows you how to extend the system.

=== "Something is broken"

    Jump to [Troubleshooting](guides/troubleshooting/email-delivery-issues.md) for the most common issues, or check [Health Checks](monitoring/health-checks.md) to diagnose service problems.

---

<div style="text-align: center; padding: 2rem 0 1rem; color: var(--md-default-fg-color--light); font-size: 0.85rem;">
  Built with :material-heart: by the Mailyte engineering team at <strong>TechiesAfrica</strong>
</div>
