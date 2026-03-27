# System Monitoring

OS-level metrics — CPU, memory, disk, and network — because your services are only as healthy as the machine they run on.

## Node Exporter

System metrics come from Prometheus Node Exporter, which runs as a container alongside everything else.

```yaml
# docker-compose.monitoring.yml
node-exporter:
  image: prom/node-exporter:latest
  pid: host
  volumes:
    - /proc:/host/proc:ro
    - /sys:/host/sys:ro
    - /:/rootfs:ro
  command:
    - "--path.procfs=/host/proc"
    - "--path.sysfs=/host/sys"
    - "--path.rootfs=/rootfs"
    - "--collector.filesystem.mount-points-exclude=^/(sys|proc|dev|host|etc)($$|/)"
  ports:
    - "9100:9100"
```

## CPU

```promql
# Overall CPU usage (percentage)
100 - (avg(irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)

# CPU usage by mode (user, system, iowait, etc.)
irate(node_cpu_seconds_total[5m]) * 100

# Load average (1, 5, 15 min)
node_load1
node_load5
node_load15
```

**What to watch for:**

| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| CPU usage | < 70% | 70-85% | > 85% sustained |
| Load average | < num CPUs | 1-2x CPUs | > 2x CPUs |
| iowait | < 5% | 5-20% | > 20% |

High `iowait` usually means disk I/O is the bottleneck — common when the mail queue is huge or MySQL is running heavy queries.

## Memory

```promql
# Memory usage percentage
(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100

# Available memory
node_memory_MemAvailable_bytes

# Memory breakdown
node_memory_MemTotal_bytes
node_memory_MemFree_bytes
node_memory_Buffers_bytes
node_memory_Cached_bytes

# Swap usage
node_memory_SwapTotal_bytes - node_memory_SwapFree_bytes
```

**What to watch for:**

| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| Memory used | < 80% | 80-90% | > 90% |
| Swap used | 0 | Any | > 1 GB |

> **Note:** Some swap usage is normal on Linux. But if your mail server is actively swapping, performance will tank. Add more RAM or reduce service memory limits.

## Disk

```promql
# Disk usage percentage
(1 - node_filesystem_avail_bytes{mountpoint="/"}
/ node_filesystem_size_bytes{mountpoint="/"}) * 100

# Available disk space
node_filesystem_avail_bytes{mountpoint="/"}

# Disk I/O rate (bytes/sec)
rate(node_disk_read_bytes_total[5m])
rate(node_disk_written_bytes_total[5m])

# Disk I/O operations per second
rate(node_disk_reads_completed_total[5m])
rate(node_disk_writes_completed_total[5m])

# Disk I/O latency
rate(node_disk_read_time_seconds_total[5m])
/ rate(node_disk_reads_completed_total[5m])
```

**Critical mount points to monitor:**

| Mount Point | Contains | Fills Up When |
|-------------|----------|---------------|
| `/` | OS, containers | Logs grow unchecked |
| `/var/lib/docker` | Container storage | Images pile up |
| `/var/mail` or mail volume | Mailboxes | Users don't clean up |
| `/var/lib/mysql` or DB volume | Database | Tables grow, no cleanup |

```bash
# Quick check from the command line
df -h /
df -h /var/lib/docker
docker system df
```

> **Warning:** A full disk is the #1 cause of cascading failures. MySQL crashes, Postfix can't queue mail, logs stop writing. Set up disk alerts early and aggressively.

## Network

```promql
# Network throughput (bytes/sec)
rate(node_network_receive_bytes_total{device="eth0"}[5m])
rate(node_network_transmit_bytes_total{device="eth0"}[5m])

# Network errors
rate(node_network_receive_errs_total{device="eth0"}[5m])
rate(node_network_transmit_errs_total{device="eth0"}[5m])

# TCP connections by state
node_netstat_Tcp_CurrEstab
node_netstat_Tcp_ActiveOpens
```

**What to watch for:**

| Metric | Normal | Investigate |
|--------|--------|-------------|
| Bandwidth | Steady pattern | Sudden spikes |
| Network errors | 0 | Any |
| Established connections | Stable | Climbing |
| TIME_WAIT connections | < 1000 | > 5000 |

## Container Resource Usage

Docker exposes per-container stats. Use cAdvisor or Docker's built-in metrics.

```bash
# Real-time container stats
docker stats --no-stream --format \
  "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}"
```

**With cAdvisor (recommended for Prometheus):**

```yaml
cadvisor:
  image: gcr.io/cadvisor/cadvisor:latest
  volumes:
    - /:/rootfs:ro
    - /var/run:/var/run:ro
    - /sys:/sys:ro
    - /var/lib/docker/:/var/lib/docker:ro
  ports:
    - "8081:8080"
```

```promql
# Container CPU usage
rate(container_cpu_usage_seconds_total{name=~"mailyte.*"}[5m]) * 100

# Container memory usage
container_memory_usage_bytes{name=~"mailyte.*"}

# Container network I/O
rate(container_network_receive_bytes_total{name=~"mailyte.*"}[5m])
rate(container_network_transmit_bytes_total{name=~"mailyte.*"}[5m])
```

## Resource Limits

Set container resource limits in Docker Compose to prevent any one service from starving the others:

```yaml
services:
  postfix:
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 1G
        reservations:
          cpus: "0.5"
          memory: 256M

  mysql:
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 2G
        reservations:
          cpus: "1.0"
          memory: 512M

  api:
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 512M
        reservations:
          cpus: "0.25"
          memory: 128M
```

## Grafana Dashboard Suggestions

Build a system overview dashboard with these panels:

```
+----------------------------+----------------------------+
|       CPU Usage (%)        |      Memory Usage (%)      |
|       (time series)        |       (time series)        |
+----------------------------+----------------------------+
|     Disk Usage by Mount    |    Network Throughput      |
|       (bar gauge)          |       (time series)        |
+----------------------------+----------------------------+
|   Container CPU Usage      |  Container Memory Usage    |
|   (stacked time series)    |   (stacked time series)    |
+----------------------------+----------------------------+
|              Disk I/O (read + write, time series)       |
+---------------------------------------------------------+
```

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
