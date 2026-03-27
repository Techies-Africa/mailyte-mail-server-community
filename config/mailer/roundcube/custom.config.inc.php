<?php

/**
 * Mailyte Roundcube Custom Configuration
 *
 * Uses STARTTLS on port 587 with user authentication.
 * This allows sending to both local and external recipients.
 */

// STARTTLS on submission port — requires authentication
$config['smtp_host'] = 'tls://postfix:587';
$config['smtp_auth_type'] = 'PLAIN';
$config['smtp_user'] = '%u';
$config['smtp_pass'] = '%p';

// Accept self-signed certs (internal Docker network)
$config['smtp_conn_options'] = array(
    'ssl' => array(
        'verify_peer' => false,
        'verify_peer_name' => false,
        'allow_self_signed' => true,
    ),
);
