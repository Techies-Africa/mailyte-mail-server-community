# Installation Guide

This guide will walk you through installing and configuring the Mailyte Mail Server on your infrastructure.

## Prerequisites

### System Requirements

#### Minimum Requirements
- **OS**: Ubuntu 20.04+ or CentOS 8+
- **CPU**: 2 cores
- **RAM**: 4GB
- **Storage**: 50GB SSD
- **Network**: 100Mbps

#### Recommended for Production
- **OS**: Ubuntu 22.04 LTS
- **CPU**: 8 cores
- **RAM**: 16GB
- **Storage**: 500GB NVMe SSD
- **Network**: 1Gbps

### Software Dependencies

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Install required packages
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    mysql-server \
    nginx \
    git \
    curl \
    wget \
    unzip

# Install Docker (recommended for production)
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
```

## Installation Methods

### Method 1: Quick Start (Recommended)

1. **Clone the Repository**
```bash
git clone https://github.com/your-org/enterprise-mail-server.git
cd enterprise-mail-server
```

2. **Configure Environment**
```bash
cp .env.example .env
nano .env
```

Edit the key configuration values:
```bash
# Domain Configuration
HOSTNAME=mail.yourdomain.com
DOMAIN=yourdomain.com

# Database Configuration
DB_HOST=localhost
DB_NAME=mailserver
DB_USER=mailserver
DB_PASSWORD=secure_password

# Admin Configuration
ADMIN_EMAIL=admin@yourdomain.com
ADMIN_PASSWORD=admin_secure_password

# SSL Configuration
ACME_EMAIL=admin@yourdomain.com
ACME_STAGING=false
```

3. **Run Installation Script**
```bash
python3 main.py --install
```

The installer will:
- Set up the database schema
- Configure all services
- Generate SSL certificates
- Start all components

### Method 2: Manual Installation

#### Step 1: Database Setup

```bash
# Secure MySQL installation
sudo mysql_secure_installation

# Create database and user
sudo mysql -u root -p << EOF
CREATE DATABASE mailserver CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'mailserver'@'localhost' IDENTIFIED BY 'secure_password';
GRANT ALL PRIVILEGES ON mailserver.* TO 'mailserver'@'localhost';
FLUSH PRIVILEGES;
EOF
```

#### Step 2: Python Environment

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

#### Step 3: Database Migration

```bash
# Run database migrations
python3 database/migrate.py
```

#### Step 4: Service Configuration

Configure each service by editing the respective configuration files:

- **Postfix**: `mailer/postfix/config/main.cf`
- **Dovecot**: `mailer/dovecot/config/dovecot.conf`
- **Nginx**: Configure virtual hosts for API and web interfaces

#### Step 5: Start Services

```bash
# Start the main application
python3 main.py
```

### Method 3: Docker Installation

1. **Clone Repository**
```bash
git clone https://github.com/your-org/enterprise-mail-server.git
cd enterprise-mail-server
```

2. **Configure Environment**
```bash
cp .env.example .env
# Edit .env with your settings
```

3. **Start with Docker Compose**
```bash
docker-compose up -d
```

## Post-Installation Configuration

### 1. DNS Configuration

Configure the following DNS records for your domain:

```dns
# A Records
mail.yourdomain.com.     IN A     YOUR_SERVER_IP
smtp.yourdomain.com.     IN A     YOUR_SERVER_IP
imap.yourdomain.com.     IN A     YOUR_SERVER_IP

# MX Record
yourdomain.com.          IN MX 10 mail.yourdomain.com.

# TXT Records (SPF)
yourdomain.com.          IN TXT   "v=spf1 mx include:mail.yourdomain.com ~all"

# TXT Records (DMARC)
_dmarc.yourdomain.com.   IN TXT   "v=DMARC1; p=quarantine; rua=mailto:dmarc@yourdomain.com"

# CNAME for tracking
track.yourdomain.com.    IN CNAME mail.yourdomain.com.
```

### 2. SSL Certificate Setup

The system automatically obtains SSL certificates from Let's Encrypt:

```bash
# Check certificate status
python3 -c "
from mailer.cert_manager.scripts.cert_manager import CertManager
cm = CertManager()
cm.check_all_certificates()
"
```

### 3. Firewall Configuration

```bash
# Configure UFW firewall
sudo ufw allow ssh
sudo ufw allow 25/tcp    # SMTP
sudo ufw allow 587/tcp   # SMTP Submission
sudo ufw allow 993/tcp   # IMAPS
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable
```

### 4. Create First Organization

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Default Organization",
    "external_id": "default-org",
    "limits": {
      "inbound_hourly": 1000,
      "outbound_hourly": 1000
    }
  }' \
  http://localhost:5000/api/v1/organizations
```

### 5. Add Your First Domain

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "domain": "yourdomain.com",
    "organization_id": 1,
    "external_id": "domain-1"
  }' \
  http://localhost:5000/api/v1/domains
```

### 6. Create Administrator Mailbox

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@yourdomain.com",
    "password": "admin_password",
    "domain_id": 1,
    "external_id": "admin-user",
    "quota_mb": 5000,
    "is_admin": true
  }' \
  http://localhost:5000/api/v1/mailboxes
```

## Verification

### 1. Service Health Check

```bash
# Check all services
curl http://localhost:8080/health

# Check individual services
curl http://localhost:5000/api/v1/health     # API Gateway
curl http://localhost:8081/health            # Webhooks
curl http://localhost:8082/health            # Rate Limiter
curl http://localhost:8083/health            # Tracking
curl http://localhost:8084/health            # Storage
curl http://localhost:8090/health            # RAG Service
```

### 2. Email Functionality Test

```bash
# Test SMTP connection
telnet localhost 25

# Test IMAP connection
telnet localhost 143

# Send test email
echo "Test email body" | mail -s "Test Subject" admin@yourdomain.com
```

### 3. API Test

```bash
# Test API endpoints
curl http://localhost:5000/api/v1/organizations
curl http://localhost:5000/api/v1/domains
curl http://localhost:5000/api/v1/mailboxes
```

## Monitoring Setup

### 1. Enable Monitoring

```bash
# Start monitoring stack
cd worker/monitoring
docker-compose -f docker-compose.monitoring.yml up -d
```

### 2. Access Dashboards

- **Grafana**: http://localhost:3000 (admin/admin)
- **Prometheus**: http://localhost:9090
- **Health Monitor**: http://localhost:8080

## Troubleshooting

### Common Issues

#### 1. Database Connection Failed
```bash
# Check MySQL service
sudo systemctl status mysql

# Test connection
mysql -u mailserver -p mailserver
```

#### 2. SSL Certificate Issues
```bash
# Check certificate status
sudo certbot certificates

# Renew certificates manually
sudo certbot renew
```

#### 3. Email Delivery Issues
```bash
# Check Postfix logs
tail -f /var/log/mail.log

# Test SMTP
echo "test" | mail -s "test" test@example.com
```

#### 4. Service Port Conflicts
```bash
# Check port usage
sudo netstat -tulpn | grep :25
sudo netstat -tulpn | grep :5000
```

### Log Locations

- **Application Logs**: `logs/`
- **Postfix Logs**: `/var/log/mail.log`
- **Dovecot Logs**: `/var/log/dovecot.log`
- **Nginx Logs**: `/var/log/nginx/`

## Next Steps

1. **Configure Webhooks**: Set up real-time notifications
2. **Enable Tracking**: Configure email tracking features
3. **Set Up Monitoring**: Configure alerts and dashboards
4. **Security Hardening**: Review security best practices
5. **Backup Configuration**: Set up automated backups

## Support

If you encounter issues during installation:

1. Check the [Troubleshooting Guide](../troubleshooting.md)
2. Review the logs for error messages
3. Visit our [GitHub Issues](https://github.com/your-org/enterprise-mail-server/issues)
4. Contact support at [support@mailserver.example.com](mailto:support@mailserver.example.com)

## Security Considerations

- Change all default passwords
- Configure firewall rules appropriately
- Set up SSL certificates for production
- Enable fail2ban for intrusion detection
- Regular security updates
- Monitor access logs regularly

The installation is now complete! Your Mailyte Mail Server is ready to handle email traffic with advanced tracking, webhooks, and monitoring capabilities.