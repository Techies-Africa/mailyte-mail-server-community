#!/bin/bash

# Dovecot quota warning script with storage usage service integration
# This script is called when a user's quota reaches certain thresholds

USER="$1"
PERCENT="$2"

# Check if we have required parameters
if [ -z "$USER" ] || [ -z "$PERCENT" ]; then
    echo "Usage: $0 <user> <percent>"
    exit 1
fi

# Log the quota warning
echo "$(date): Quota warning for $USER at $PERCENT%" >> /var/log/dovecot-quota.log

# Update storage usage service with quota warning
update_storage_usage_quota() {
    local user="$1"
    local percent="$2"

    # Extract domain from email address
    domain=$(echo "$user" | cut -d'@' -f2)

    # Send quota alert to storage usage service
    curl -s -X POST \
        -H "Content-Type: application/json" \
        -d "{
            \"entity_type\": \"mailbox\",
            \"identifier\": \"$user\",
            \"quota_percentage\": $percent,
            \"alert_source\": \"dovecot_quota_warning\"
        }" \
        "${STORAGE_USAGE_SERVICE_URL:-http://localhost:8084}/storage/alerts/mailbox/$user" \
        >/dev/null 2>&1

    if [ $? -eq 0 ]; then
        echo "$(date): Storage usage service notified for $user at $percent%" >> /var/log/dovecot-quota.log
    else
        echo "$(date): Failed to notify storage usage service for $user" >> /var/log/dovecot-quota.log
    fi
}

# Trigger storage usage calculation if quota is critical
trigger_storage_calculation() {
    local user="$1"
    local percent="$2"

    # Trigger real-time calculation if quota is over 90%
    if [ "$percent" -ge 90 ]; then
        curl -s -X POST \
            "${STORAGE_USAGE_SERVICE_URL:-http://localhost:8084}/storage/calculate/mailbox/$user" \
            >/dev/null 2>&1

        if [ $? -eq 0 ]; then
            echo "$(date): Storage calculation triggered for $user" >> /var/log/dovecot-quota.log
        fi
    fi
}

# Update storage usage service
update_storage_usage_quota "$USER" "$PERCENT"
trigger_storage_calculation "$USER" "$PERCENT"

# Send warning email to user
cat << EOF | /usr/sbin/sendmail "$USER"
From: postmaster@$(hostname)
To: $USER
Subject: Mailbox Quota Warning - $PERCENT% Full

Dear User,