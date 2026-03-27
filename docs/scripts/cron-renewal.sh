
#!/bin/bash
# Cron script for SSL certificate renewal
# Runs daily at 2 AM

# Set environment
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Log file
LOG_FILE="/var/log/docs-ssl/cron-renewal.log"

# Function to log with timestamp
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG_FILE"
}

log "Starting daily SSL certificate check"

# Run certificate renewal check
python3 /usr/local/bin/docs-ssl-manager.py renew >> "$LOG_FILE" 2>&1

if [ $? -eq 0 ]; then
    log "SSL certificate check completed successfully"
else
    log "SSL certificate check failed with exit code $?"
fi

log "Daily SSL certificate check finished"
