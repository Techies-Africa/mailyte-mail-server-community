---
title: Prometheus Metrics
description: Every Prometheus metric exposed by Mailyte services, with type, labels, and descriptions.
---

# Prometheus Metrics

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


All Mailyte services expose Prometheus metrics on their `/metrics` endpoint. This is the complete list.

## API Metrics

Exposed on `api:8080/metrics`.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mailyte_api_requests_total` | Counter | `method`, `endpoint`, `status` | Total API requests |
| `mailyte_api_request_duration_seconds` | Histogram | `method`, `endpoint` | Request latency |
| `mailyte_api_active_connections` | Gauge | — | Current active connections |
| `mailyte_api_errors_total` | Counter | `endpoint`, `error_type` | Total API errors |

## Email Metrics

Exposed across mail-related workers.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mailyte_emails_sent_total` | Counter | `organization_id`, `domain` | Emails sent |
| `mailyte_emails_received_total` | Counter | `organization_id`, `domain` | Emails received |
| `mailyte_emails_bounced_total` | Counter | `organization_id`, `domain`, `bounce_type` | Emails bounced |
| `mailyte_emails_rejected_total` | Counter | `organization_id`, `reason` | Emails rejected by Rspamd/policy |
| `mailyte_emails_deferred_total` | Counter | `organization_id` | Emails deferred |
| `mailyte_mail_queue_size` | Gauge | `status` | Current queue depth by status |
| `mailyte_email_size_bytes` | Histogram | `direction` | Email size distribution |
| `mailyte_delivery_duration_seconds` | Histogram | `destination_domain` | Time from queue to delivery |

## Tracking Metrics

Exposed on `tracking:8086/metrics`.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mailyte_tracking_events_total` | Counter | `event_type`, `organization_id` | Tracking events processed |
| `mailyte_tracking_opens_total` | Counter | `organization_id`, `domain` | Email opens |
| `mailyte_tracking_clicks_total` | Counter | `organization_id`, `domain` | Link clicks |
| `mailyte_tracking_bounces_total` | Counter | `organization_id`, `bounce_type` | Bounces tracked |
| `mailyte_tracking_processing_duration_seconds` | Histogram | `event_type` | Event processing time |

## Webhook Metrics

Exposed on `webhooks:8081/metrics`.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mailyte_webhook_deliveries_total` | Counter | `status`, `event_type` | Webhook delivery attempts |
| `mailyte_webhook_delivery_duration_seconds` | Histogram | `endpoint` | Delivery round-trip time |
| `mailyte_webhook_queue_size` | Gauge | — | Pending webhook deliveries |
| `mailyte_webhook_retries_total` | Counter | `endpoint` | Retry attempts |
| `mailyte_webhook_failures_total` | Counter | `endpoint`, `http_status` | Failed deliveries |

## Rate Limiter Metrics

Exposed on `rate-limiter:8084/metrics`.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mailyte_rate_limit_checks_total` | Counter | `organization_id`, `result` | Rate limit checks (allowed/denied) |
| `mailyte_rate_limit_exceeded_total` | Counter | `organization_id`, `limit_type` | Rate limit exceeded events |
| `mailyte_rate_limit_usage_ratio` | Gauge | `organization_id`, `limit_type` | Current usage as ratio (0.0-1.0) |

## Analytics Metrics

Exposed on `analytics:8082/metrics`.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mailyte_analytics_aggregations_total` | Counter | `metric_name`, `period` | Aggregation runs |
| `mailyte_analytics_processing_duration_seconds` | Histogram | `operation` | Processing time |

## Queue Manager Metrics

Exposed on `queue-manager:8085/metrics`.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mailyte_queue_processed_total` | Counter | `status` | Messages processed |
| `mailyte_queue_processing_duration_seconds` | Histogram | — | Per-message processing time |
| `mailyte_queue_active_workers` | Gauge | — | Currently busy worker threads |
| `mailyte_queue_oldest_message_age_seconds` | Gauge | — | Age of the oldest queued message |

## Storage Metrics

Exposed on `storage-usage:8087/metrics`.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mailyte_storage_used_bytes` | Gauge | `organization_id`, `domain`, `level` | Storage usage |
| `mailyte_storage_quota_bytes` | Gauge | `organization_id`, `domain`, `level` | Storage quota |
| `mailyte_storage_usage_ratio` | Gauge | `organization_id`, `level` | Usage as ratio (0.0-1.0) |
| `mailyte_storage_calculation_duration_seconds` | Histogram | — | Time to recalculate storage |

## Monitoring Service Metrics

Exposed on `monitoring:8088/metrics`.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mailyte_health_check_status` | Gauge | `service` | 1 = healthy, 0 = unhealthy |
| `mailyte_health_check_duration_seconds` | Histogram | `service` | Health check latency |
| `mailyte_ssl_cert_expiry_days` | Gauge | `domain` | Days until cert expires |
| `mailyte_service_uptime_seconds` | Gauge | `service` | Time since last restart |

## RAG / AI Search Metrics

Exposed on `rag:8089/metrics`.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mailyte_rag_queries_total` | Counter | `organization_id` | Search queries |
| `mailyte_rag_query_duration_seconds` | Histogram | — | Query latency |
| `mailyte_rag_index_operations_total` | Counter | `operation` | Index add/delete/update |
| `mailyte_rag_vectors_total` | Gauge | `organization_id` | Total vectors in Qdrant |

## Infrastructure Metrics (via Exporters)

### Node Exporter (host metrics)

| Metric | Description |
|--------|-------------|
| `node_cpu_seconds_total` | CPU time |
| `node_memory_MemAvailable_bytes` | Available RAM |
| `node_filesystem_avail_bytes` | Disk space available |
| `node_disk_io_time_seconds_total` | Disk I/O time |
| `node_network_receive_bytes_total` | Network bytes received |
| `node_network_transmit_bytes_total` | Network bytes sent |

### MySQL Exporter

| Metric | Description |
|--------|-------------|
| `mysql_global_status_threads_connected` | Active connections |
| `mysql_global_variables_max_connections` | Max connections |
| `mysql_global_status_slow_queries` | Slow query count |
| `mysql_global_status_innodb_buffer_pool_read_requests` | Buffer pool reads |
| `mysql_global_status_innodb_buffer_pool_reads` | Buffer pool misses |

### Redis Exporter

| Metric | Description |
|--------|-------------|
| `redis_memory_used_bytes` | Redis memory usage |
| `redis_memory_max_bytes` | Redis max memory |
| `redis_connected_clients` | Connected clients |
| `redis_keyspace_hits_total` | Cache hits |
| `redis_keyspace_misses_total` | Cache misses |
| `redis_commands_processed_total` | Total commands |

## Useful PromQL Queries

```promql
# Email throughput (emails/minute)
rate(mailyte_emails_sent_total[5m]) * 60

# Bounce rate
rate(mailyte_emails_bounced_total[1h]) / rate(mailyte_emails_sent_total[1h])

# API latency p95
histogram_quantile(0.95, rate(mailyte_api_request_duration_seconds_bucket[5m]))

# API error rate
rate(mailyte_api_errors_total[5m]) / rate(mailyte_api_requests_total[5m])

# Storage usage percentage
mailyte_storage_used_bytes / mailyte_storage_quota_bytes

# Redis hit rate
rate(redis_keyspace_hits_total[5m]) / (rate(redis_keyspace_hits_total[5m]) + rate(redis_keyspace_misses_total[5m]))

# MySQL buffer pool hit rate
rate(mysql_global_status_innodb_buffer_pool_read_requests[5m]) / (rate(mysql_global_status_innodb_buffer_pool_read_requests[5m]) + rate(mysql_global_status_innodb_buffer_pool_reads[5m]))
```
