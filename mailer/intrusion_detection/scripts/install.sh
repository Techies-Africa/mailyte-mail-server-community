
#!/bin/bash
"""
Fail2Ban Installation and Configuration Script
Sets up comprehensive intrusion detection for mail server
"""

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   error "This script must be run as root"
fi

log "🔧 Installing Fail2Ban for Mail Server Protection..."

# Install fail2ban
log "📦 Installing fail2ban package..."
if command -v apt-get &> /dev/null; then
    apt-get update
    apt-get install -y fail2ban
elif command -v yum &> /dev/null; then
    yum install -y fail2ban
elif command -v dnf &> /dev/null; then
    dnf install -y fail2ban
else
    error "Unsupported package manager. Please install fail2ban manually."
fi

# Create directory structure
log "📁 Creating directory structure..."
mkdir -p /etc/fail2ban/filter.d
mkdir -p /etc/fail2ban/action.d
mkdir -p /usr/local/bin
mkdir -p /var/log/fail2ban

# Copy configuration files
log "📋 Installing configuration files..."

# Main jail configuration
cat > /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
# Ban time in seconds (30 minutes)
bantime = 1800
findtime = 600
maxretry = 5
backend = auto
destemail = admin@yourdomain.com
sender = fail2ban@yourdomain.com
mta = sendmail
action = %(action_mwl)s

# Postfix Authentication Failures
[postfix-auth]
enabled = true
port = smtp,465,587
filter = postfix-auth
logpath = /var/log/mail.log
maxretry = 3
bantime = 3600
findtime = 600

# Dovecot Authentication Failures
[dovecot-auth]
enabled = true
port = pop3,pop3s,imap,imaps
filter = dovecot-auth
logpath = /var/log/dovecot.log
maxretry = 3
bantime = 3600
findtime = 600

# Postfix Spam Attempts
[postfix-spam]
enabled = true
port = smtp,465,587
filter = postfix-spam
logpath = /var/log/mail.log
maxretry = 10
bantime = 7200
findtime = 3600

# Rate Limiting Violations
[rate-limit]
enabled = true
port = smtp,465,587
filter = rate-limit
logpath = /var/log/mail.log
maxretry = 5
bantime = 1800
findtime = 300
EOF

# Install filter files
log "🔍 Installing detection filters..."

# Postfix auth filter
cat > /etc/fail2ban/filter.d/postfix-auth.conf << 'EOF'
[Definition]
failregex = ^.* postfix/smtpd\[.*\]: warning: <HOST>\[.*\]: SASL (?:LOGIN|PLAIN|(?:CRAM|DIGEST)-MD5) authentication failed.*$
            ^.* postfix/submission/smtpd\[.*\]: warning: <HOST>\[.*\]: SASL (?:LOGIN|PLAIN|(?:CRAM|DIGEST)-MD5) authentication failed.*$
ignoreregex = authentication successful
EOF

# Dovecot auth filter  
cat > /etc/fail2ban/filter.d/dovecot-auth.conf << 'EOF'
[Definition]
failregex = ^.* dovecot: (?:auth|auth-worker).*authentication failed.*rip=<HOST>.*$
            ^.* dovecot: imap-login: (?:Disconnected|Aborted login).*rip=<HOST>.*$
            ^.* dovecot: pop3-login: (?:Disconnected|Aborted login).*rip=<HOST>.*$
ignoreregex = login successful
EOF

# Postfix spam filter
cat > /etc/fail2ban/filter.d/postfix-spam.conf << 'EOF'
[Definition]
failregex = ^.* postfix/smtpd\[.*\]: NOQUEUE: reject: .*from .*\[<HOST>\].*$
            ^.* postfix/cleanup\[.*\]: .*from .*\[<HOST>\].*: reject.*$
ignoreregex = 
EOF

# Rate limit filter
cat > /etc/fail2ban/filter.d/rate-limit.conf << 'EOF'
[Definition]
failregex = ^.* rate_limit_policy: REJECT .*from.*\[<HOST>\].*rate limit exceeded.*$
            ^.* postfix/anvil\[.*\]: statistics: max .* rate .*\[<HOST>\].*$
ignoreregex = 
EOF

# Install webhook action
cat > /etc/fail2ban/action.d/webhook.conf << 'EOF'
[Definition]
actionban = /usr/local/bin/fail2ban-webhook.py ban <ip> <name> <bantime>
actionunban = /usr/local/bin/fail2ban-webhook.py unban <ip> <name>
actioncheck = 
actionstart = 
actionstop = 

[Init]
name = webhook
init = webhook
EOF

# Install webhook script
log "🔗 Installing webhook notification script..."
cp "$(dirname "$0")/fail2ban-webhook.py" /usr/local/bin/
cp "$(dirname "$0")/fail2ban-manager.py" /usr/local/bin/
chmod +x /usr/local/bin/fail2ban-webhook.py
chmod +x /usr/local/bin/fail2ban-manager.py

# Install Python dependencies
log "🐍 Installing Python dependencies..."
if command -v pip3 &> /dev/null; then
    pip3 install requests mysql-connector-python redis
else
    warn "pip3 not found. Please install Python dependencies manually:"
    echo "  - requests"
    echo "  - mysql-connector-python" 
    echo "  - redis"
fi

# Create log rotation
log "📋 Setting up log rotation..."
cat > /etc/logrotate.d/fail2ban-custom << 'EOF'
/var/log/fail2ban-webhook.log {
    weekly
    missingok
    rotate 52
    compress
    delaycompress
    notifempty
    create 0644 root root
}
EOF

# Enable and start fail2ban
log "🚀 Starting fail2ban service..."
systemctl enable fail2ban
systemctl start fail2ban

# Configure firewall if available
if command -v ufw &> /dev/null; then
    log "🔒 Configuring UFW firewall..."
    ufw --force enable
    ufw allow ssh
    ufw allow 25/tcp   # SMTP
    ufw allow 587/tcp  # Submission
    ufw allow 465/tcp  # SMTPS
    ufw allow 993/tcp  # IMAPS
    ufw allow 995/tcp  # POP3S
    ufw allow 143/tcp  # IMAP
    ufw allow 110/tcp  # POP3
fi

# Test configuration
log "🧪 Testing fail2ban configuration..."
if fail2ban-client status &> /dev/null; then
    log "✅ Fail2ban is running successfully!"
    
    # Show status
    echo ""
    log "📊 Current jail status:"
    fail2ban-client status
    
else
    error "❌ Fail2ban configuration test failed!"
fi

# Create monitoring script
log "📈 Creating monitoring script..."
cat > /usr/local/bin/fail2ban-monitor.sh << 'EOF'
#!/bin/bash
# Fail2Ban Monitoring Script

# Check fail2ban status
if ! systemctl is-active --quiet fail2ban; then
    echo "CRITICAL: Fail2ban service is not running!"
    systemctl restart fail2ban
fi

# Generate daily report
/usr/local/bin/fail2ban-manager.py --report > /var/log/fail2ban-daily-report.log

# Send webhook if too many IPs are banned (potential attack)
BANNED_COUNT=$(fail2ban-client status | grep "Currently banned" | awk '{print $3}' || echo "0")
if [ "$BANNED_COUNT" -gt 50 ]; then
    /usr/local/bin/fail2ban-webhook.py alert "mass_attack" "high_ban_count" "$BANNED_COUNT"
fi
EOF

chmod +x /usr/local/bin/fail2ban-monitor.sh

# Add monitoring to cron
log "⏰ Setting up monitoring cron job..."
(crontab -l 2>/dev/null; echo "0 */6 * * * /usr/local/bin/fail2ban-monitor.sh") | crontab -

log "✅ Fail2Ban installation completed successfully!"
log ""
log "🔧 Configuration Summary:"
log "  - Main config: /etc/fail2ban/jail.local"
log "  - Filters: /etc/fail2ban/filter.d/"
log "  - Actions: /etc/fail2ban/action.d/"
log "  - Logs: /var/log/fail2ban.log"
log "  - Webhook script: /usr/local/bin/fail2ban-webhook.py"
log "  - Manager script: /usr/local/bin/fail2ban-manager.py"
log ""
log "📋 Next steps:"
log "  1. Review and customize /etc/fail2ban/jail.local"
log "  2. Set SECURITY_WEBHOOK_URL environment variable"
log "  3. Test with: fail2ban-client status"
log "  4. Monitor logs: tail -f /var/log/fail2ban.log"
log ""
log "⚡ Quick commands:"
log "  - Check status: fail2ban-client status"
log "  - Unban IP: fail2ban-client set <jail> unbanip <ip>"
log "  - Get report: /usr/local/bin/fail2ban-manager.py --report"
