
#!/bin/bash

# Dovecot webhook notification script
# This script is called by Dovecot for various IMAP/POP3 events

WEBHOOK_URL=${WEBHOOK_SERVICE_URL:-"http://webhooks:8081"}

send_imap_webhook() {
    local action=$1
    local user=$2
    local mailbox=$3
    local message_uid=$4
    local client_ip=$5
    local connection_id=$6
    local organization_id=${7:-"default"}
    
    curl -s -X POST "${WEBHOOK_URL}/webhook/email/imap" \
        -H "Content-Type: application/json" \
        -d "{
            \"action\": \"${action}\",
            \"user\": \"${user}\",
            \"mailbox\": \"${mailbox}\",
            \"message_uid\": \"${message_uid}\",
            \"client_ip\": \"${client_ip}\",
            \"connection_id\": \"${connection_id}\",
            \"organization_id\": \"${organization_id}\"
        }" &
}

send_pop3_webhook() {
    local action=$1
    local user=$2
    local client_ip=$3
    local connection_id=$4
    local messages_downloaded=${5:-0}
    local bytes_downloaded=${6:-0}
    local organization_id=${7:-"default"}
    
    curl -s -X POST "${WEBHOOK_URL}/webhook/email/pop3" \
        -H "Content-Type: application/json" \
        -d "{
            \"action\": \"${action}\",
            \"user\": \"${user}\",
            \"client_ip\": \"${client_ip}\",
            \"connection_id\": \"${connection_id}\",
            \"messages_downloaded\": ${messages_downloaded},
            \"bytes_downloaded\": ${bytes_downloaded},
            \"organization_id\": \"${organization_id}\"
        }" &
}

# Main execution
case "$1" in
    "imap")
        send_imap_webhook "$2" "$3" "$4" "$5" "$6" "$7" "$8"
        ;;
    "pop3")
        send_pop3_webhook "$2" "$3" "$4" "$5" "$6" "$7" "$8"
        ;;
    *)
        echo "Usage: $0 [imap|pop3] [action] [user] [additional_params...] [organization_id]"
        exit 1
        ;;
esac
