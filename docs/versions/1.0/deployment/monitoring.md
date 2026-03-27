
# Monitoring and Observability

This guide covers comprehensive monitoring, alerting, and observability for the Mailyte Mail Server in production environments.

## 🎯 **Monitoring Overview**

### **Monitoring Strategy**
The Mailyte Mail Server implements a multi-layered monitoring approach:

1. **Infrastructure Monitoring**: System resources, network, storage
2. **Application Monitoring**: Service health, performance metrics
3. **Business Monitoring**: Email delivery, user activity, revenue metrics
4. **Security Monitoring**: Threats, intrusions, compliance

### **Key Principles**
- **Proactive Monitoring**: Detect issues before they impact users
- **Comprehensive Coverage**: Monitor all critical components
- **Actionable Alerts**: Every alert should require action
- **Historical Analysis**: Trend analysis and capacity planning
- **Real-time Visibility**: Dashboards for immediate insight

## 📊 **Built-in Monitoring**

### **Health Monitor Service**
The built-in health monitor provides comprehensive system monitoring:

```bash
# Access health monitor dashboard
http://your-server:8080

# Health check API
curl http://your-server:8080/health

# Individual service health
curl http://your-server:8080/services/postfix
curl http://your-server:8080/services/dovecot
curl http://your-server:8080/services/api
```

### **Service Metrics**
Each service exposes Prometheus-compatible metrics:

```bash
# Get metrics from any service
curl http://your-server:8090/metrics  # Your service
curl http://your-server:5000/metrics  # API service
curl http://your-server:8083/metrics  # Tracking service
```

### **Health Check Endpoints**
Standard health check format across all services:

```json
{
  "status": "healthy|unhealthy",
  "service": "service_name",
  "version": "1.0.0",
  "timestamp": "2024-01-01T12:00:00Z",
  "checks": {
    "database": true,
    "dependencies": true,
    "resources": true
  },
  "metrics": {
    "uptime_seconds": 86400,
    "requests_total": 1000,
    "errors_total": 5,
    "memory_usage_mb": 128
  }
}
```

## 🔧 **Prometheus Setup**

### **Prometheus Configuration**
Create `monitoring/prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "rules/*.yml"

alerting:
  alertmanagers:
    - static_configs:
        - targets:
          - alertmanager:9093

scrape_configs:
  # Mail Server Health Monitor
  - job_name: 'health-monitor'
    static_configs:
      - targets: ['mail-server:8080']
    metrics_path: /metrics
    scrape_interval: 30s

  # Core Mail Services
  - job_name: 'mail-services'
    static_configs:
      - targets: 
        - 'mail-server:25'    # Postfix
        - 'mail-server:993'   # Dovecot IMAPS
        - 'mail-server:587'   # Postfix submission
    metrics_path: /metrics
    scrape_interval: 30s

  # Worker Services
  - job_name: 'worker-services'
    static_configs:
      - targets:
        - 'mail-server:5000'   # API
        - 'mail-server:8081'   # Webhooks
        - 'mail-server:8082'   # Rate Limiter
        - 'mail-server:8083'   # Tracking
        - 'mail-server:8084'   # Storage
        - 'mail-server:8085'   # RAG
        - 'mail-server:8086'   # Queue Manager
        - 'mail-server:8087'   # Analytics
    metrics_path: /metrics
    scrape_interval: 15s

  # Database Monitoring
  - job_name: 'mysql'
    static_configs:
      - targets: ['mysql-server:9104']
    scrape_interval: 30s

  # Redis Monitoring
  - job_name: 'redis'
    static_configs:
      - targets: ['redis-server:9121']
    scrape_interval: 30s

  # Node Exporter (System Metrics)
  - job_name: 'node'
    static_configs:
      - targets: ['mail-server:9100']
    scrape_interval: 30s

  # Blackbox Exporter (External Monitoring)
  - job_name: 'blackbox'
    metrics_path: /probe
    params:
      module: [http_2xx]
    static_configs:
      - targets:
        - http://mail-server:8080/health
        - http://mail-server:5000/api/v1/health
        - https://your-domain.com
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: blackbox-exporter:9115
```

### **Alert Rules**
Create `monitoring/rules/mail-server.yml`:

```yaml
groups:
  - name: mail-server-alerts
    rules:
      # Service Health Alerts
      - alert: ServiceDown
        expr: up == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Service {{ $labels.job }} is down"
          description: "Service {{ $labels.job }} on {{ $labels.instance }} has been down for more than 1 minute"

      # Mail Service Specific Alerts
      - alert: PostfixDown
        expr: up{job="mail-services", instance=~".*:25"} == 0
        for: 30s
        labels:
          severity: critical
        annotations:
          summary: "Postfix SMTP server is down"
          description: "Postfix SMTP service is not responding"

      - alert: DovecotDown
        expr: up{job="mail-services", instance=~".*:993"} == 0
        for: 30s
        labels:
          severity: critical
        annotations:
          summary: "Dovecot IMAP server is down"
          description: "Dovecot IMAP service is not responding"

      # Performance Alerts
      - alert: HighEmailQueueSize
        expr: email_queue_size > 1000
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Email queue size is high"
          description: "Email queue has {{ $value }} messages pending"

      - alert: HighErrorRate
        expr: rate(http_requests_total{status=~"5.."}[5m]) > 0.1
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "High error rate detected"
          description: "Error rate is {{ $value }} requests/second"

      # Resource Alerts
      - alert: HighMemoryUsage
        expr: (node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / node_memory_MemTotal_bytes > 0.85
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High memory usage"
          description: "Memory usage is {{ $value | humanizePercentage }}"

      - alert: HighDiskUsage
        expr: (node_filesystem_size_bytes - node_filesystem_free_bytes) / node_filesystem_size_bytes > 0.80
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High disk usage"
          description: "Disk usage is {{ $value | humanizePercentage }} on {{ $labels.mountpoint }}"

      # Security Alerts
      - alert: HighFailedLogins
        expr: increase(failed_login_attempts_total[5m]) > 50
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "High number of failed login attempts"
          description: "{{ $value }} failed login attempts in the last 5 minutes"

      # Business Metric Alerts
      - alert: LowEmailDeliveryRate
        expr: email_delivery_success_rate < 0.95
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Email delivery rate is low"
          description: "Email delivery success rate is {{ $value | humanizePercentage }}"

      - alert: HighSpamRate
        expr: spam_detection_rate > 0.20
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "High spam detection rate"
          description: "Spam detection rate is {{ $value | humanizePercentage }}"
```

## 📈 **Grafana Dashboards**

### **Main Mail Server Dashboard**
Create comprehensive Grafana dashboard with panels for:

#### **Overview Panel**
```json
{
  "title": "Mail Server Overview",
  "panels": [
    {
      "title": "Service Status",
      "type": "stat",
      "targets": [
        {
          "expr": "up{job=\"mail-services\"}",
          "legendFormat": "{{instance}}"
        }
      ]
    },
    {
      "title": "Email Throughput",
      "type": "graph",
      "targets": [
        {
          "expr": "rate(emails_processed_total[5m])",
          "legendFormat": "Emails/sec"
        }
      ]
    },
    {
      "title": "Queue Size",
      "type": "graph",
      "targets": [
        {
          "expr": "email_queue_size",
          "legendFormat": "Queue Size"
        }
      ]
    }
  ]
}
```

#### **Performance Panel**
```json
{
  "title": "Performance Metrics",
  "panels": [
    {
      "title": "Response Time",
      "type": "graph",
      "targets": [
        {
          "expr": "histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))",
          "legendFormat": "95th percentile"
        }
      ]
    },
    {
      "title": "Memory Usage",
      "type": "graph",
      "targets": [
        {
          "expr": "process_resident_memory_bytes",
          "legendFormat": "{{instance}}"
        }
      ]
    }
  ]
}
```

#### **Business Metrics Panel**
```json
{
  "title": "Business Metrics",
  "panels": [
    {
      "title": "Email Delivery Rate",
      "type": "singlestat",
      "targets": [
        {
          "expr": "email_delivery_success_rate",
          "legendFormat": "Success Rate"
        }
      ]
    },
    {
      "title": "Active Domains",
      "type": "singlestat",
      "targets": [
        {
          "expr": "active_domains_total",
          "legendFormat": "Domains"
        }
      ]
    },
    {
      "title": "Active Mailboxes",
      "type": "singlestat",
      "targets": [
        {
          "expr": "active_mailboxes_total",
          "legendFormat": "Mailboxes"
        }
      ]
    }
  ]
}
```

### **Security Dashboard**
Create dedicated security monitoring dashboard:

```json
{
  "title": "Security Monitoring",
  "panels": [
    {
      "title": "Failed Login Attempts",
      "type": "graph",
      "targets": [
        {
          "expr": "rate(failed_login_attempts_total[5m])",
          "legendFormat": "Failed Logins/sec"
        }
      ]
    },
    {
      "title": "Blocked IPs",
      "type": "table",
      "targets": [
        {
          "expr": "fail2ban_blocked_ips",
          "legendFormat": "{{ip}}"
        }
      ]
    },
    {
      "title": "Spam Detection",
      "type": "graph",
      "targets": [
        {
          "expr": "rate(spam_detected_total[5m])",
          "legendFormat": "Spam/sec"
        }
      ]
    }
  ]
}
```

## 🚨 **Alertmanager Configuration**

### **Alertmanager Setup**
Create `monitoring/alertmanager.yml`:

```yaml
global:
  smtp_smarthost: 'your-smtp-server:587'
  smtp_from: 'alerts@your-domain.com'
  smtp_auth_username: 'alerts@your-domain.com'
  smtp_auth_password: 'your-password'

route:
  group_by: ['alertname']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 1h
  receiver: 'web.hook'
  routes:
    - match:
        severity: critical
      receiver: 'critical-alerts'
    - match:
        severity: warning
      receiver: 'warning-alerts'

receivers:
  - name: 'web.hook'
    webhook_configs:
      - url: 'http://your-webhook-endpoint'

  - name: 'critical-alerts'
    email_configs:
      - to: 'ops-team@your-domain.com'
        subject: '🚨 CRITICAL: {{ .GroupLabels.alertname }}'
        body: |
          {{ range .Alerts }}
          Alert: {{ .Annotations.summary }}
          Description: {{ .Annotations.description }}
          {{ end }}
    slack_configs:
      - api_url: 'your-slack-webhook-url'
        channel: '#alerts'
        title: 'Critical Alert'
        text: '{{ range .Alerts }}{{ .Annotations.summary }}{{ end }}'

  - name: 'warning-alerts'
    email_configs:
      - to: 'dev-team@your-domain.com'
        subject: '⚠️  WARNING: {{ .GroupLabels.alertname }}'
        body: |
          {{ range .Alerts }}
          Alert: {{ .Annotations.summary }}
          Description: {{ .Annotations.description }}
          {{ end }}

inhibit_rules:
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: ['alertname', 'dev', 'instance']
```

## 📱 **Custom Monitoring Tools**

### **Health Check Script**
Create `monitoring/health_check.py`:

```python
#!/usr/bin/env python3
"""
Custom health check script for Mailyte Mail Server
"""
import requests
import json
import sys
import time
from datetime import datetime

class HealthChecker:
    def __init__(self, config):
        self.config = config
        self.results = []
    
    def check_service(self, name, url, timeout=10):
        """Check individual service health"""
        try:
            start_time = time.time()
            response = requests.get(url, timeout=timeout)
            response_time = (time.time() - start_time) * 1000
            
            if response.status_code == 200:
                data = response.json()
                status = data.get('status', 'unknown')
                
                result = {
                    'service': name,
                    'status': status,
                    'response_time_ms': round(response_time, 2),
                    'timestamp': datetime.utcnow().isoformat(),
                    'healthy': status == 'healthy'
                }
                
                if 'checks' in data:
                    result['checks'] = data['checks']
                
                self.results.append(result)
                return result['healthy']
            else:
                self.results.append({
                    'service': name,
                    'status': 'unhealthy',
                    'error': f'HTTP {response.status_code}',
                    'timestamp': datetime.utcnow().isoformat(),
                    'healthy': False
                })
                return False
                
        except Exception as e:
            self.results.append({
                'service': name,
                'status': 'unhealthy',
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat(),
                'healthy': False
            })
            return False
    
    def run_checks(self):
        """Run all health checks"""
        services = [
            ('Health Monitor', 'http://localhost:8080/health'),
            ('API Gateway', 'http://localhost:5000/api/v1/health'),
            ('Webhooks', 'http://localhost:8081/health'),
            ('Rate Limiter', 'http://localhost:8082/health'),
            ('Tracking', 'http://localhost:8083/health'),
            ('Storage', 'http://localhost:8084/health'),
            ('RAG', 'http://localhost:8085/health'),
            ('Queue Manager', 'http://localhost:8086/health'),
            ('Analytics', 'http://localhost:8087/health'),
        ]
        
        healthy_count = 0
        total_count = len(services)
        
        for name, url in services:
            if self.check_service(name, url):
                healthy_count += 1
        
        overall_health = {
            'overall_status': 'healthy' if healthy_count == total_count else 'unhealthy',
            'healthy_services': healthy_count,
            'total_services': total_count,
            'health_percentage': round((healthy_count / total_count) * 100, 2),
            'timestamp': datetime.utcnow().isoformat(),
            'details': self.results
        }
        
        return overall_health
    
    def report(self, output_format='json'):
        """Generate health report"""
        health_data = self.run_checks()
        
        if output_format == 'json':
            print(json.dumps(health_data, indent=2))
        elif output_format == 'summary':
            print(f"Overall Status: {health_data['overall_status']}")
            print(f"Healthy Services: {health_data['healthy_services']}/{health_data['total_services']}")
            print(f"Health Percentage: {health_data['health_percentage']}%")
            
            for result in health_data['details']:
                status_icon = "✅" if result['healthy'] else "❌"
                print(f"{status_icon} {result['service']}: {result['status']}")
        
        # Exit with error code if not fully healthy
        if health_data['overall_status'] != 'healthy':
            sys.exit(1)

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Mail Server Health Check')
    parser.add_argument('--format', choices=['json', 'summary'], 
                       default='summary', help='Output format')
    parser.add_argument('--config', help='Configuration file path')
    
    args = parser.parse_args()
    
    config = {}
    if args.config:
        with open(args.config, 'r') as f:
            config = json.load(f)
    
    checker = HealthChecker(config)
    checker.report(args.format)
```

### **Performance Monitor**
Create `monitoring/performance_monitor.py`:

```python
#!/usr/bin/env python3
"""
Performance monitoring script
"""
import psutil
import requests
import json
import time
from datetime import datetime

class PerformanceMonitor:
    def __init__(self):
        self.metrics = {}
    
    def collect_system_metrics(self):
        """Collect system-level metrics"""
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        self.metrics['system'] = {
            'cpu_percent': cpu_percent,
            'memory_percent': memory.percent,
            'memory_used_gb': round(memory.used / (1024**3), 2),
            'memory_total_gb': round(memory.total / (1024**3), 2),
            'disk_percent': round((disk.used / disk.total) * 100, 2),
            'disk_used_gb': round(disk.used / (1024**3), 2),
            'disk_total_gb': round(disk.total / (1024**3), 2),
            'load_average': psutil.getloadavg(),
            'timestamp': datetime.utcnow().isoformat()
        }
    
    def collect_service_metrics(self):
        """Collect service-specific metrics"""
        services = {
            'api': 'http://localhost:5000/metrics',
            'webhooks': 'http://localhost:8081/metrics',
            'tracking': 'http://localhost:8083/metrics',
        }
        
        self.metrics['services'] = {}
        
        for service, url in services.items():
            try:
                response = requests.get(url, timeout=5)
                if response.status_code == 200:
                    # Parse Prometheus metrics
                    metrics = self.parse_prometheus_metrics(response.text)
                    self.metrics['services'][service] = metrics
            except Exception as e:
                self.metrics['services'][service] = {'error': str(e)}
    
    def parse_prometheus_metrics(self, metrics_text):
        """Parse Prometheus metrics format"""
        metrics = {}
        for line in metrics_text.split('\n'):
            if line and not line.startswith('#'):
                parts = line.split(' ')
                if len(parts) == 2:
                    try:
                        metrics[parts[0]] = float(parts[1])
                    except ValueError:
                        metrics[parts[0]] = parts[1]
        return metrics
    
    def generate_report(self):
        """Generate performance report"""
        self.collect_system_metrics()
        self.collect_service_metrics()
        
        return {
            'timestamp': datetime.utcnow().isoformat(),
            'system': self.metrics['system'],
            'services': self.metrics['services']
        }

if __name__ == '__main__':
    monitor = PerformanceMonitor()
    report = monitor.generate_report()
    print(json.dumps(report, indent=2))
```

## 🔍 **Log Monitoring**

### **Centralized Logging with ELK Stack**
Set up Elasticsearch, Logstash, and Kibana for log aggregation:

#### **Filebeat Configuration**
```yaml
filebeat.inputs:
  - type: log
    enabled: true
    paths:
      - /var/log/mail.log
      - /var/log/postfix.log
      - /var/log/dovecot.log
      - /app/logs/*.log
    fields:
      service: mail-server
      environment: production

output.logstash:
  hosts: ["logstash:5044"]

processors:
  - add_host_metadata:
      when.not.contains.tags: forwarded
```

### **Log Analysis Queries**

#### **Common Postfix Queries**
```bash
# Find bounced emails
grep "bounced" /var/log/mail.log

# Check for authentication failures
grep "authentication failed" /var/log/mail.log

# Monitor queue size
postqueue -p | tail -1
```

#### **Security Event Detection**
```bash
# Failed login attempts
grep "authentication failure" /var/log/auth.log

# Intrusion attempts
tail -f /var/log/fail2ban.log

# Unusual access patterns
grep "POST" /var/log/nginx/access.log | awk '{print $1}' | sort | uniq -c | sort -nr
```

## 📊 **Business Intelligence**

### **Email Analytics Dashboard**
Track business-critical metrics:

- **Delivery Rates**: Success/failure rates by domain
- **Response Times**: API and service response times
- **Usage Patterns**: Peak usage times and trends
- **Revenue Metrics**: Cost per email, ROI analysis
- **User Satisfaction**: Support ticket correlation

### **Capacity Planning**
Monitor growth trends:

- **Storage Growth**: Email storage usage over time
- **Compute Usage**: CPU and memory trends
- **Network Traffic**: Bandwidth utilization
- **Database Performance**: Query performance trends

## 🚀 **Deployment**

### **Docker Compose Monitoring Stack**
```yaml
version: '3.8'
services:
  prometheus:
    image: prom/prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
      - ./monitoring/rules:/etc/prometheus/rules

  grafana:
    image: grafana/grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - grafana-data:/var/lib/grafana

  alertmanager:
    image: prom/alertmanager
    ports:
      - "9093:9093"
    volumes:
      - ./monitoring/alertmanager.yml:/etc/alertmanager/alertmanager.yml

volumes:
  grafana-data:
```

### **Monitoring Deployment Script**
```bash
#!/bin/bash
# Deploy monitoring stack

echo "Deploying monitoring infrastructure..."

# Create monitoring directory
mkdir -p monitoring/{prometheus,grafana,alertmanager}

# Deploy Prometheus
docker-compose -f monitoring/docker-compose.yml up -d prometheus

# Deploy Grafana
docker-compose -f monitoring/docker-compose.yml up -d grafana

# Deploy Alertmanager
docker-compose -f monitoring/docker-compose.yml up -d alertmanager

echo "Monitoring stack deployed successfully"
echo "Prometheus: http://localhost:9090"
echo "Grafana: http://localhost:3000 (admin/admin)"
echo "Alertmanager: http://localhost:9093"
```

This comprehensive monitoring setup provides enterprise-grade observability for your mail server deployment, ensuring high availability and performance.
