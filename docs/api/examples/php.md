# PHP Examples

PHP examples using the built-in cURL functions.

## Setup

Create a reusable helper class:

```php
<?php

class MailyteClient
{
    private string $baseUrl;
    private string $apiKey;

    public function __construct(string $baseUrl, string $apiKey)
    {
        $this->baseUrl = rtrim($baseUrl, '/');
        $this->apiKey = $apiKey;
    }

    /**
     * Make an API request.
     *
     * @param string $method  HTTP method (GET, POST, PUT, DELETE)
     * @param string $path    API path (e.g., /api/v1/organizations)
     * @param array|null $data  Request body (for POST/PUT)
     * @param array $params   Query parameters (for GET)
     * @return array          Decoded JSON response
     * @throws Exception      On HTTP or API error
     */
    public function request(string $method, string $path, ?array $data = null, array $params = []): array
    {
        $url = $this->baseUrl . $path;

        if (!empty($params)) {
            $url .= '?' . http_build_query($params);
        }

        $ch = curl_init();
        curl_setopt_array($ch, [
            CURLOPT_URL => $url,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_CUSTOMREQUEST => $method,
            CURLOPT_HTTPHEADER => [
                'X-API-Key: ' . $this->apiKey,
                'Content-Type: application/json',
            ],
            CURLOPT_TIMEOUT => 30,
        ]);

        if ($data !== null) {
            curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($data));
        }

        $response = curl_exec($ch);
        $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $error = curl_error($ch);
        curl_close($ch);

        if ($error) {
            throw new Exception("cURL error: $error");
        }

        $decoded = json_decode($response, true);

        if ($httpCode >= 400) {
            $msg = $decoded['msg'] ?? 'Unknown error';
            throw new Exception("HTTP $httpCode: $msg");
        }

        if (($decoded['type'] ?? '') === 'error') {
            throw new Exception("API error: " . ($decoded['msg'] ?? 'Unknown'));
        }

        return $decoded;
    }

    // Convenience methods
    public function get(string $path, array $params = []): array
    {
        return $this->request('GET', $path, null, $params);
    }

    public function post(string $path, array $data): array
    {
        return $this->request('POST', $path, $data);
    }

    public function put(string $path, array $data): array
    {
        return $this->request('PUT', $path, $data);
    }

    public function delete(string $path): array
    {
        return $this->request('DELETE', $path);
    }
}
```

## Initialize the Client

```php
$client = new MailyteClient(
    'http://your-server:5000',
    'mk_live_your_api_key_here'
);
```

## Organization Examples

### Create an Organization

```php
$org = $client->post('/api/v1/organizations', [
    'id' => 'acme',
    'name' => 'Acme Corp',
    'admin_email' => 'admin@acme.com',
    'rate_limits' => [
        'inbound_hourly' => 5000,
        'outbound_hourly' => 2000,
    ],
    'storage_quotas' => [
        'storage_quota_mb' => 51200,
    ],
]);

echo "Created: " . $org['data']['id'] . "\n";
```

### List Organizations

```php
$orgs = $client->get('/api/v1/organizations', ['page' => 1, 'per_page' => 25]);

foreach ($orgs['data']['items'] as $org) {
    echo "{$org['id']}: {$org['name']} ({$org['domain_count']} domains)\n";
}

echo "Total: {$orgs['data']['pagination']['total']}\n";
```

### Get Organization Details

```php
$org = $client->get('/api/v1/organizations/acme');

echo "Name: {$org['data']['name']}\n";
echo "Domains: {$org['data']['domain_count']}\n";
echo "Accounts: {$org['data']['email_account_count']}\n";
echo "Storage: {$org['data']['storage_statistics']['usage_percentage']}%\n";
```

### Update an Organization

```php
$updated = $client->put('/api/v1/organizations/acme', [
    'name' => 'Acme Corporation',
    'rate_limits' => [
        'inbound_hourly' => 10000,
        'outbound_hourly' => 5000,
    ],
]);

echo "Updated: {$updated['data']['name']}\n";
```

## Domain Examples

### Create a Domain

```php
$domain = $client->post('/api/v1/domains', [
    'domain' => 'acme.com',
    'organization_id' => 'acme',
    'max_quota' => 10737418240,
    'max_users' => 500,
    'dkim_enabled' => true,
]);

echo "Domain created: {$domain['data']['domain']} (ID: {$domain['data']['id']})\n";
```

### List Domains

```php
$domains = $client->get('/api/v1/domains', ['organization_id' => 'acme']);

foreach ($domains['data']['items'] as $d) {
    echo "{$d['domain']} - {$d['email_account_count']} accounts\n";
}
```

## Email Account Examples

### Create an Account

```php
$account = $client->post('/api/v1/email-accounts', [
    'email' => 'john@acme.com',
    'password' => 'SecurePass123',
    'name' => 'John Doe',
    'storage_quota' => 2147483648,
]);

echo "Created: {$account['data']['email']}\n";
```

### Create Multiple Accounts

```php
$users = [
    ['email' => 'john@acme.com', 'password' => 'SecurePass123', 'name' => 'John Doe'],
    ['email' => 'jane@acme.com', 'password' => 'SecurePass456', 'name' => 'Jane Smith'],
    ['email' => 'admin@acme.com', 'password' => 'AdminPass789', 'name' => 'Admin'],
];

foreach ($users as $user) {
    try {
        $result = $client->post('/api/v1/email-accounts', $user);
        echo "Created: {$result['data']['email']}\n";
    } catch (Exception $e) {
        echo "Failed: {$user['email']} - {$e->getMessage()}\n";
    }
}
```

### List Accounts with Pagination

```php
$page = 1;
$allAccounts = [];

do {
    $result = $client->get('/api/v1/email-accounts', [
        'organization_id' => 'acme',
        'page' => $page,
    ]);

    $allAccounts = array_merge($allAccounts, $result['data']['items']);
    $totalPages = $result['data']['pagination']['total_pages'];
    $page++;
} while ($page <= $totalPages);

echo "Total accounts: " . count($allAccounts) . "\n";
```

### Update an Account

```php
$updated = $client->put('/api/v1/email-accounts/1', [
    'name' => 'John A. Doe',
    'forward_enabled' => true,
    'forward_destination' => 'john@gmail.com',
]);

echo "Updated: {$updated['data']['email']}\n";
```

## Alias Examples

### Create an Alias

```php
$alias = $client->post('/api/v1/add/alias', [
    'address' => 'info@acme.com',
    'goto' => 'john@acme.com, jane@acme.com',
]);

echo "Alias created: {$alias['data']['address']}\n";
```

### Bulk Create Aliases

```php
$result = $client->post('/api/v1/add/alias/bulk', [
    'aliases' => [
        ['address' => 'sales@acme.com', 'goto' => 'john@acme.com'],
        ['address' => 'support@acme.com', 'goto' => 'jane@acme.com'],
    ],
]);

echo "Created: {$result['data']['summary']['success']}/{$result['data']['summary']['total']}\n";
```

## Webhook Examples

### Register a Webhook

```php
$webhook = $client->post('/api/v1/webhooks/endpoints', [
    'url' => 'https://hooks.acme.com/mailyte',
    'description' => 'Production webhook',
    'event_types' => ['email.sent', 'email.delivered', 'email.bounced'],
]);

// Save this secret securely
echo "Secret: {$webhook['data']['secret']}\n";
```

### Test a Webhook

```php
$result = $client->post("/api/v1/webhooks/endpoints/{$webhook['data']['id']}/test", []);
echo "Test event dispatched\n";
```

## RAG Search

```php
$results = $client->post('/api/v1/rag/search', [
    'query' => 'invoices from last quarter',
    'organization_id' => 'acme',
    'limit' => 5,
]);

foreach ($results['results'] as $r) {
    echo sprintf("[%.2f] %s - %s\n", $r['score'], $r['subject'], $r['from']);
}
```

## Health Check

```php
// Health check does not require authentication
$ch = curl_init('http://your-server:5000/health');
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
$response = json_decode(curl_exec($ch), true);
curl_close($ch);

echo "Status: {$response['status']}, DB: {$response['database']}\n";
```

## Error Handling

```php
try {
    $client->post('/api/v1/organizations', [
        'id' => 'acme',
        'name' => 'Acme Corp',
    ]);
} catch (Exception $e) {
    $msg = $e->getMessage();

    if (str_starts_with($msg, 'HTTP 409')) {
        echo "Organization already exists\n";
    } elseif (str_starts_with($msg, 'HTTP 400')) {
        echo "Validation error: $msg\n";
    } elseif (str_starts_with($msg, 'HTTP 401')) {
        echo "Check your API key\n";
    } else {
        echo "Error: $msg\n";
    }
}
```

## Full Setup Script

```php
<?php
require_once 'MailyteClient.php';

$client = new MailyteClient('http://your-server:5000', 'mk_live_your_api_key_here');

echo "=== Creating organization ===\n";
$client->post('/api/v1/organizations', [
    'id' => 'acme',
    'name' => 'Acme Corp',
    'admin_email' => 'admin@acme.com',
]);

echo "=== Adding domain ===\n";
$client->post('/api/v1/domains', [
    'domain' => 'acme.com',
    'organization_id' => 'acme',
]);

echo "=== Creating accounts ===\n";
foreach (['john', 'jane', 'admin'] as $name) {
    $client->post('/api/v1/email-accounts', [
        'email' => "$name@acme.com",
        'password' => 'SecurePass123',
        'name' => ucfirst($name),
    ]);
    echo "  Created $name@acme.com\n";
}

echo "=== Creating aliases ===\n";
$client->post('/api/v1/add/alias/bulk', [
    'aliases' => [
        ['address' => 'info@acme.com', 'goto' => 'admin@acme.com'],
        ['address' => 'support@acme.com', 'goto' => 'jane@acme.com'],
    ],
]);

echo "=== Setting up webhook ===\n";
$wh = $client->post('/api/v1/webhooks/endpoints', [
    'url' => 'https://hooks.acme.com/mailyte',
    'event_types' => ['email.delivered', 'email.bounced'],
]);
echo "  Secret: {$wh['data']['secret']}\n";

echo "=== Done! ===\n";
```
