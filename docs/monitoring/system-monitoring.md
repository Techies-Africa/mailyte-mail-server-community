# System Monitoring

OS-level metrics — CPU, memory, disk, and network — because your services are only as healthy as the machine they run on.

## How System Metrics Are Collected

Mailyte does **not** deploy a Prometheus node-exporter or cAdvisor. Host-level metrics come from two psutil-based sources:

1. **The monitoring service** (`worker/monitoring/services/system_monitor.py`) — samples CPU, memory, swap, disk, load average, uptime, and network I/O every 30 seconds, applies alert thresholds, and sends webhook alerts on breaches.
2. **Every worker's own `/metrics`** — the shared collector appends `<service>_cpu_usage_percent`, `<service>_memory_usage_percent`, and `<service>_memory_usage_bytes` gauges to each scrape.

```bash
# Current system stats as JSON
curl -s http://localhost:8085/api/stats | python3 -m json.tool
```

!!! warning "node_* series do not exist"
    Any Grafana panel or alert rule written against `node_cpu_seconds_total`, `node_memory_*`, or `node_filesystem_*` (including the `DiskSpaceWarning`/`DiskSpaceCritical` rules in `mail_alerts.yml`) matches nothing, because no node-exporter runs. Disk-pressure alerting is done by the monitoring service's own thresholds below. If you want the full `node_*` catalogue, add a `prom/node-exporter` service to the compose file and a matching scrape job — both are currently absent.

## Alert Thresholds

`system_monitor.check_system_health()` applies these thresholds on every 30-second sweep and dispatches webhook alerts (`cpu_high`, `memory_warning`, `disk_high`, …):

| Resource | Warning | Critical |
|----------|---------|----------|
| CPU | > 80% | > 90% |
| Memory | > 80% | > 90% |
| Disk (per path) | > 75% | > 85% |

Disk is checked for each of `/`, `/var`, `/tmp`, `/var/log`, and `/var/spool` (paths that don't exist in the container are skipped).

## CPU

```bash
# From the monitoring service
curl -s http://localhost:8085/api/stats | python3 -c \
  "import json,sys; print(json.load(sys.stdin)['system']['cpu_usage'])"

# Classic host-side tools
top -bn1 | head -15
uptime
```

**What to watch for:**

| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| CPU usage | < 70% | 70-85% | > 85% sustained |
| Load average | < num CPUs | 1-2x CPUs | > 2x CPUs |
| iowait | < 5% | 5-20% | > 20% |

High `iowait` usually means disk I/O is the bottleneck — common when the mail queue is huge or MySQL is running heavy queries.

## Memory

**What to watch for:**

| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| Memory used | < 80% | 80-90% | > 90% |
| Swap used | 0 | Any | > 1 GB |

> **Note:** Some swap usage is normal on Linux. But if your mail server is actively swapping, performance will tank. Add more RAM or reduce service memory limits.

Per-service memory is visible in Prometheus via the `<service>_memory_usage_bytes` gauges, and per-container via `docker stats`.

## Disk

**Critical mount points to monitor:**

| Mount Point | Contains | Fills Up When |
|-------------|----------|---------------|
| `/` | OS, containers | Logs grow unchecked |
| `/var/lib/docker` | Container storage | Images pile up |
| `storage/mail_data/` | Mailboxes (bind-mounted Maildir) | Users don't clean up |
| MySQL volume | Database | Tables grow, no cleanup |

```bash
# Quick check from the command line
df -h /
df -h /var/lib/docker
docker system df
```

> **Warning:** A full disk is the #1 cause of cascading failures. MySQL crashes, Postfix can't queue mail, logs stop writing. The monitoring service alerts at 75%/85% — take the warning seriously. A full backup also writes ~3 GB locally before uploading, so keep that headroom.

## Network

There are no Prometheus network metrics without a node-exporter; the monitoring service includes `psutil.net_io_counters()` in its stats payload, and host tools cover the rest:

```bash
# Throughput and error counters
ip -s link

# TCP connections summary
ss -s

# Established connections to the mail ports
ss -tn state established '( sport = :25 or sport = :587 or sport = :993 )' | wc -l
```

| Metric | Normal | Investigate |
|--------|--------|-------------|
| Bandwidth | Steady pattern | Sudden spikes |
| Interface errors | 0 | Any |
| Established connections | Stable | Climbing |

## Container Resource Usage

```bash
# Real-time container stats
docker stats --no-stream --format \
  "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}"
```

## Resource Limits

`docker-compose.prod.yml` sets memory limits per service so no single container can starve the host — for example `dovecot: 1G`, `monitoring: 512M`. Pattern:

```yaml
services:
  monitoring:
    deploy:
      resources:
        limits:
          memory: 512M
```

Check the prod compose file for the current per-service values before changing them — an OOM-killed Dovecot is worse than a slow one.

## Quick Diagnostic Commands

When things are slow and you need answers fast:

```bash
# Top processes by CPU
top -bn1 | head -15

# Top processes by memory
ps aux --sort=-%mem | head -10

# Disk I/O right now
iostat -x 1 3

# Network connections summary
ss -s

# Open files (useful for "too many open files" errors)
lsof | wc -l
```
