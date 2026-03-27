
#!/bin/bash

##
## Dovecot Webhook Notification Script
##
## This script sends real-time webhook notifications for IMAP/POP3 events
## to the enterprise webhook system. It captures user activity, authentication
## events, and mail operations for comprehensive email tracking.
##
## Events tracked:
## - User login/logout events
## - Mail read/delete/move/flag operations  
## - Session information and client details
## - Authentication failures and security events
##
## Integration: Called by Dovecot statistics and notification plugins
##

# Configuration
WEBHOOK_URL="${WEBHOOK_URL:-http://webhooks:8081/dovecot-webhook}"
WEBHOOK_SECRET="${WEBHOOK_SECRET:-dovecot_webhook_secret_change_this}"
TIMEOUT="${WEBHOOK_TIMEOUT:-5}"
LOG_FILE="/var/log/dovecot-webhook.log"

# Function to log messages with timestamp
log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# Function to generate HMAC signature for webhook security
generate_signature() {
    local payload="$1"
    local secret="$2"
    echo -n "$payload" | openssl dgst -sha256 -hmac "$secret" -binary | base64
}

# Function to send webhook notification
send_webhook() {
    local event_type="$1"
    local payload="$2"
    
    # Add timestamp and event wrapper
    local full_payload="{
        \"event\": \"$event_type\",
        \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
        \"service\": \"dovecot\",
        \"payload\": $payload
    }"
    
    # Generate security signature
    local signature=$(generate_signature "$full_payload" "$WEBHOOK_SECRET")
    
    # Send webhook with proper headers
    curl -s -X POST \
        -H "Content-Type: application/json" \
        -H "X-Webhook-Signature: sha256=$signature" \
        -H "X-Webhook-Source: dovecot" \
        -H "User-Agent: Dovecot-Webhook/1.0" \
        -d "$full_payload" \
        --max-time "$TIMEOUT" \
        "$WEBHOOK_URL" \
        >> "$LOG_FILE" 2>&1
    
    local exit_code=$?
    if [ $exit_code -eq 0 ]; then
        log_message "Successfully sent $event_type webhook"
    else
        log_message "Failed to send $event_type webhook (exit code: $exit_code)"
    fi
}

# Function to extract client information from environment
get_client_info() {
    cat << EOF
{
    "ip_address": "${DOVECOT_CLIENT_IP:-unknown}",
    "hostname": "${DOVECOT_CLIENT_HOSTNAME:-unknown}",
    "user_agent": "${DOVECOT_CLIENT_ID:-unknown}",
    "protocol": "${DOVECOT_PROTOCOL:-unknown}",
    "connection_id": "${DOVECOT_CONNECTION_ID:-unknown}",
    "session_start": "${DOVECOT_SESSION_START:-unknown}",
    "local_ip": "${DOVECOT_LOCAL_IP:-unknown}",
    "local_port": "${DOVECOT_LOCAL_PORT:-unknown}",
    "remote_port": "${DOVECOT_REMOTE_PORT:-unknown}",
    "secured": "${DOVECOT_TLS_SECURED:-false}",
    "tls_cipher": "${DOVECOT_TLS_CIPHER:-unknown}",
    "tls_protocol": "${DOVECOT_TLS_PROTOCOL:-unknown}"
}
EOF
}

# Function to handle login events
handle_login_event() {
    local username="$1"
    local success="$2"
    local reason="${3:-}"
    
    local payload="{
        \"action\": \"login\",
        \"username\": \"$username\",
        \"success\": $success,
        \"failure_reason\": \"$reason\",
        \"client_info\": $(get_client_info),
        \"auth_method\": \"${DOVECOT_AUTH_METHOD:-unknown}\",
        \"session_id\": \"${DOVECOT_SESSION_ID:-$(date +%s)-$$}\"
    }"
    
    if [ "$success" = "true" ]; then
        send_webhook "imap.login" "$payload"
        log_message "Login success for user: $username from ${DOVECOT_CLIENT_IP:-unknown}"
    else
        send_webhook "imap.failed_login" "$payload"
        log_message "Login failure for user: $username from ${DOVECOT_CLIENT_IP:-unknown} - $reason"
    fi
}

# Function to handle logout events
handle_logout_event() {
    local username="$1"
    local session_duration="${2:-0}"
    local bytes_in="${3:-0}"
    local bytes_out="${4:-0}"
    
    local payload="{
        \"action\": \"logout\",
        \"username\": \"$username\",
        \"session_duration_seconds\": $session_duration,
        \"bytes_received\": $bytes_in,
        \"bytes_sent\": $bytes_out,
        \"client_info\": $(get_client_info),
        \"session_id\": \"${DOVECOT_SESSION_ID:-unknown}\"
    }"
    
    send_webhook "imap.logout" "$payload"
    log_message "Logout for user: $username (session: ${session_duration}s, in: ${bytes_in}b, out: ${bytes_out}b)"
}

# Function to handle mail operation events
handle_mail_event() {
    local username="$1"
    local action="$2"
    local mailbox="$3"
    local message_uid="${4:-}"
    local message_id="${5:-}"
    local flags="${6:-}"
    
    local payload="{
        \"action\": \"$action\",
        \"username\": \"$username\",
        \"mailbox\": \"$mailbox\",
        \"message_uid\": \"$message_uid\",
        \"message_id\": \"$message_id\",
        \"flags\": \"$flags\",
        \"client_info\": $(get_client_info),
        \"session_id\": \"${DOVECOT_SESSION_ID:-unknown}\"
    }"
    
    case "$action" in
        "read"|"fetch")
            send_webhook "imap.read" "$payload"
            ;;
        "delete"|"expunge")
            send_webhook "imap.delete" "$payload"
            ;;
        "move"|"copy")
            send_webhook "imap.move" "$payload"
            ;;
        "flag"|"store")
            send_webhook "imap.flag" "$payload"
            ;;
        "search")
            send_webhook "imap.search" "$payload"
            ;;
        *)
            send_webhook "imap.$action" "$payload"
            ;;
    esac
    
    log_message "Mail event: $action by $username on $mailbox (UID: $message_uid)"
}

# Function to handle POP3 events
handle_pop3_event() {
    local username="$1"
    local action="$2"
    local message_count="${3:-0}"
    local bytes_downloaded="${4:-0}"
    
    local payload="{
        \"action\": \"$action\",
        \"username\": \"$username\",
        \"message_count\": $message_count,
        \"bytes_downloaded\": $bytes_downloaded,
        \"client_info\": $(get_client_info),
        \"session_id\": \"${DOVECOT_SESSION_ID:-$(date +%s)-$$}\"
    }"
    
    case "$action" in
        "login")
            send_webhook "pop3.login" "$payload"
            ;;
        "logout")
            send_webhook "pop3.logout" "$payload"
            ;;
        "download"|"retr")
            send_webhook "pop3.download" "$payload"
            ;;
        "delete"|"dele")
            send_webhook "pop3.delete" "$payload"
            ;;
        *)
            send_webhook "pop3.$action" "$payload"
            ;;
    esac
    
    log_message "POP3 event: $action by $username (messages: $message_count, bytes: $bytes_downloaded)"
}

# Main script logic based on command line arguments
case "${1:-}" in
    "login")
        handle_login_event "$2" "true" ""
        ;;
    "failed_login")
        handle_login_event "$2" "false" "${3:-Authentication failed}"
        ;;
    "logout")
        handle_logout_event "$2" "${3:-0}" "${4:-0}" "${5:-0}"
        ;;
    "mail_event")
        handle_mail_event "$2" "$3" "$4" "$5" "$6" "$7"
        ;;
    "pop3_event")
        handle_pop3_event "$2" "$3" "$4" "$5"
        ;;
    "quota_warning")
        # Handle quota warnings
        local payload="{
            \"action\": \"quota_warning\",
            \"username\": \"$2\",
            \"quota_percentage\": \"${3:-unknown}\",
            \"client_info\": $(get_client_info)
        }"
        send_webhook "imap.quota_warning" "$payload"
        log_message "Quota warning for user: $2 (${3:-unknown}% used)"
        ;;
    *)
        log_message "Unknown event type: ${1:-none}"
        echo "Usage: $0 {login|failed_login|logout|mail_event|pop3_event|quota_warning} [parameters...]"
        exit 1
        ;;
esac

exit 0
