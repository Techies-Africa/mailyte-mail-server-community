# Laravel API (mailyte-api) ↔ Email Server Integration Plan

!!! note "Status: historical plan — the integration is live"
    This page is the original integration plan, kept for context. As of 2026-08-30 the integration it describes is implemented and in production: mailyte-api provisions orgs/domains/mailboxes through this server's gateway, and this server fires signed webhooks back (see `shared/webhook_dispatcher.py` for the full event catalogue — it covers far more than the handful listed below). The PHP snippets are illustrative, not the shipped code, and endpoint paths shown here may lag the live API — the [API Reference](../api/index.md) is authoritative. Note in particular that mailbox creation is `POST /api/v1/mailboxes/email-accounts`, and organization creation requires a **platform-scoped** API key.

## Overview

The Mailyte platform has two backend systems:

1. **mailyte-api** (Laravel 12) — User-facing REST API. Handles authentication, billing, subscriptions, user management, and the web dashboard.
2. **mailyte-email-server** (FastAPI + Postfix/Dovecot) — Email infrastructure. Handles actual mail delivery, IMAP/POP3, spam filtering, tracking, and email server management.

These two systems need to communicate bidirectionally:
- **Laravel → Email Server:** Provision domains, mailboxes, aliases when users sign up or make changes
- **Email Server → Laravel:** Webhook notifications for mail events, tracking data, quota warnings

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    Frontend (mailyte-web)                  │
│                    Next.js / React 19                      │
└────────────────────────┬─────────────────────────────────┘
                         │ HTTPS
                         ▼
┌──────────────────────────────────────────────────────────┐
│                   Laravel API (mailyte-api)                │
│                                                           │
│  Users / Auth / Billing / Subscriptions / Dashboard       │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  EmailServerClient (HTTP client to email server)     │ │
│  │                                                      │ │
│  │  POST /api/v1/organizations/           → create org  │ │
│  │  POST /api/v1/domains/            → provision domain │ │
│  │  POST /api/v1/mailboxes/email-accounts → mailbox     │ │
│  │  POST /api/v1/aliases/            → create alias     │ │
│  │  GET  /api/v1/analytics/...       → fetch metrics    │ │
│  │  GET  /api/v1/domains/... (DNS verification)         │ │
│  └─────────────────────────────────────────────────────┘ │
│                         │                                  │
│           HTTP (internal network)                          │
│                         ▼                                  │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  Webhook Receiver (Laravel controller)               │ │
│  │                                                      │ │
│  │  POST /webhooks/mailyte                              │ │
│  │  ← email.delivered, email.bounced, tracking.open     │ │
│  │  ← storage.quota.warning, auth.login.failure         │ │
│  │  → Updates Laravel DB (usage, notifications, logs)   │ │
│  └─────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
                         │
              Internal Docker Network
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│              Email Server (mailyte-email-server)           │
│                                                           │
│  FastAPI Gateway (bind port 8080; api.<domain> in prod)   │
│  Postfix SMTP (25, 587, 465)                              │
│  Dovecot IMAP/POP3 (143/993, 110/995)                     │
│  Rspamd (anti-spam + DKIM signing)                        │
│  Tracking / log_ingestor (mail_logs producer)             │
│  Webhook Dispatcher (signed, retried, dead-lettered)      │
└──────────────────────────────────────────────────────────┘
```

---

## Implementation Steps

### Step 1: Create EmailServerClient in Laravel (1 day)

Create a service class in Laravel that wraps the email server API.

**File:** `mailyte-api/app/Services/EmailServerClient.php`

```php
<?php

namespace App\Services;

use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;

class EmailServerClient
{
    private string $baseUrl;
    private string $apiKey;

    public function __construct()
    {
        $this->baseUrl = config('services.email_server.url', 'http://api:8080');
        $this->apiKey = config('services.email_server.api_key');
    }

    private function request()
    {
        return Http::baseUrl($this->baseUrl)
            ->withHeaders([
                'X-API-Key' => $this->apiKey,
                'Accept' => 'application/json',
            ])
            ->timeout(30);
    }

    // ─── Organizations ───────────────────────────────────────

    public function createOrganization(array $data): array
    {
        $response = $this->request()->post('/api/v1/organizations', $data);
        return $response->json();
    }

    public function getOrganization(string $id): array
    {
        $response = $this->request()->get("/api/v1/organizations/{$id}");
        return $response->json();
    }

    // ─── Domains ─────────────────────────────────────────────

    public function createDomain(string $domain, string $organizationId): array
    {
        $response = $this->request()->post('/api/v1/domains', [
            'domain' => $domain,
            'organization_id' => $organizationId,
            'dkim_enabled' => true,
        ]);
        return $response->json();
        // Response includes dns_records and dkim_record
    }

    public function listDomains(string $organizationId = null): array
    {
        $params = $organizationId ? ['organization_id' => $organizationId] : [];
        $response = $this->request()->get('/api/v1/domains', $params);
        return $response->json();
    }

    public function deleteDomain(int $domainId): array
    {
        $response = $this->request()->delete("/api/v1/domains/{$domainId}");
        return $response->json();
    }

    public function verifyDns(int $domainId): array
    {
        $response = $this->request()->get("/api/v1/domains/{$domainId}/verify-dns");
        return $response->json();
    }

    // ─── Mailboxes ───────────────────────────────────────────

    public function createMailbox(array $data): array
    {
        $response = $this->request()->post('/api/v1/email-accounts', $data);
        return $response->json();
    }

    public function listMailboxes(string $organizationId = null): array
    {
        $params = $organizationId ? ['organization_id' => $organizationId] : [];
        $response = $this->request()->get('/api/v1/email-accounts', $params);
        return $response->json();
    }

    public function deleteMailbox(int $accountId): array
    {
        $response = $this->request()->delete("/api/v1/email-accounts/{$accountId}");
        return $response->json();
    }

    public function updateMailboxQuota(int $accountId, int $quotaBytes): array
    {
        $response = $this->request()->put("/api/v1/email-accounts/{$accountId}/quotas", [
            'storage_quota' => $quotaBytes,
        ]);
        return $response->json();
    }

    // ─── Aliases ─────────────────────────────────────────────

    public function createAlias(string $source, string $destination): array
    {
        $response = $this->request()->post('/api/v1/aliases', [
            'source' => $source,
            'destination' => $destination,
        ]);
        return $response->json();
    }

    // ─── Analytics ───────────────────────────────────────────

    public function getDomainAnalytics(string $domain): array
    {
        $response = $this->request()->get("/api/v1/analytics/dashboard/{$domain}");
        return $response->json();
    }

    public function getDeliverability(string $domain): array
    {
        $response = $this->request()->get("/api/v1/analytics/deliverability/{$domain}");
        return $response->json();
    }

    // ─── Tracking ────────────────────────────────────────────

    public function getTrackingStats(string $domain): array
    {
        $response = $this->request()->get("/api/v1/tracking/stats/domain/{$domain}");
        return $response->json();
    }

    // ─── Storage ─────────────────────────────────────────────

    public function getDomainUsage(string $domain): array
    {
        $response = $this->request()->get("/api/v1/storage/usage/domain/{$domain}");
        return $response->json();
    }

    public function getMailboxUsage(string $email): array
    {
        $response = $this->request()->get("/api/v1/storage/usage/mailbox/{$email}");
        return $response->json();
    }
}
```

**Config:** `mailyte-api/config/services.php`
```php
'email_server' => [
    'url' => env('EMAIL_SERVER_URL', 'http://api:8080'),
    'api_key' => env('EMAIL_SERVER_API_KEY'),
],
```

**ENV:** `mailyte-api/.env`
```
EMAIL_SERVER_URL=http://api:8080
EMAIL_SERVER_API_KEY=your-api-key
```

---

### Step 2: Wire Laravel Controllers to Email Server (2 days)

Update existing Laravel controllers to call the EmailServerClient when users manage their email infrastructure.

**Organization onboarding flow:**

```php
// app/Http/Controllers/OrganizationController.php

use App\Services\EmailServerClient;

public function store(Request $request)
{
    // 1. Create org in Laravel DB (existing logic)
    $org = Organization::create($request->validated());

    // 2. Provision org in email server
    $emailServer = app(EmailServerClient::class);
    $result = $emailServer->createOrganization([
        'name' => $org->name,
        'external_id' => $org->id,
        'admin_email' => $org->owner->email,
    ]);

    // 3. Store email server org ID for future reference
    $org->update(['email_server_org_id' => $result['data']['id'] ?? null]);

    return response()->json($org);
}
```

**Domain provisioning flow:**

```php
// app/Http/Controllers/DomainController.php

public function store(Request $request)
{
    // 1. Validate domain ownership (optional: DNS TXT verification)

    // 2. Create domain in Laravel DB
    $domain = Domain::create($request->validated());

    // 3. Provision in email server (returns DNS records + DKIM key)
    $emailServer = app(EmailServerClient::class);
    $result = $emailServer->createDomain(
        $domain->name,
        $domain->organization->email_server_org_id
    );

    // 4. Store DNS records for the user to configure
    $domain->update([
        'dns_records' => $result['data']['dns_records'] ?? [],
        'dkim_record' => $result['data']['dkim_record'] ?? null,
        'email_server_domain_id' => $result['data']['id'] ?? null,
    ]);

    return response()->json([
        'domain' => $domain,
        'dns_records' => $result['data']['dns_records'],
        'message' => 'Domain added. Configure the DNS records below, then click Verify.',
    ]);
}

public function verifyDns(Domain $domain)
{
    $emailServer = app(EmailServerClient::class);
    $verification = $emailServer->verifyDns($domain->email_server_domain_id);

    // Update domain verification status
    $allPassed = collect($verification['verification'] ?? [])
        ->every(fn($check) => $check['status'] === 'pass');

    $domain->update([
        'dns_verified' => $allPassed,
        'dns_verification_result' => $verification['verification'],
    ]);

    return response()->json($verification);
}
```

**Mailbox creation flow:**

```php
// app/Http/Controllers/MailboxController.php

public function store(Request $request)
{
    // 1. Check plan limits (max mailboxes per plan)
    $this->authorize('create', Mailbox::class);

    // 2. Create in Laravel DB
    $mailbox = Mailbox::create($request->validated());

    // 3. Provision in email server
    $emailServer = app(EmailServerClient::class);
    $result = $emailServer->createMailbox([
        'email' => $mailbox->email,
        'password' => $request->password,
        'name' => $mailbox->name,
        'domain_id' => $mailbox->domain->email_server_domain_id,
    ]);

    // 4. Return IMAP/SMTP connection settings
    return response()->json([
        'mailbox' => $mailbox,
        'connection_settings' => [
            'imap_host' => config('services.email_server.imap_host', 'imap.mailyte.com'),
            'imap_port' => 993,
            'smtp_host' => config('services.email_server.smtp_host', 'smtp.mailyte.com'),
            'smtp_port' => 587,
            'username' => $mailbox->email,
            'encryption' => 'TLS',
        ],
    ]);
}
```

---

### Step 3: Webhook Receiver in Laravel (1 day)

Create a webhook controller that receives events from the email server.

**Route:** `mailyte-api/routes/api.php`
```php
Route::post('/webhooks/mailyte', [WebhookController::class, 'handleMailyte'])
    ->middleware('verify.mailyte.webhook');
```

**Controller:** `mailyte-api/app/Http/Controllers/WebhookController.php`
```php
<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\Log;
use App\Events\EmailDelivered;
use App\Events\EmailBounced;
use App\Events\TrackingEvent;
use App\Events\QuotaWarning;

class WebhookController extends Controller
{
    public function handleMailyte(Request $request)
    {
        $event = $request->input('event');
        $data = $request->input('data', []);
        $timestamp = $request->input('timestamp');

        Log::info("Mailyte webhook: {$event}", $data);

        match ($event) {
            // Email lifecycle
            'email.delivered' => $this->handleDelivered($data),
            'email.bounced' => $this->handleBounced($data),
            'email.deferred' => $this->handleDeferred($data),

            // Tracking
            'tracking.open' => $this->handleTrackingOpen($data),
            'tracking.click' => $this->handleTrackingClick($data),

            // Storage
            'storage.quota.warning' => $this->handleQuotaWarning($data),

            // Security
            'auth.login.failure' => $this->handleAuthFailure($data),
            'security.brute_force' => $this->handleBruteForce($data),

            // Rate limiting
            'rate_limit.exceeded' => $this->handleRateLimitExceeded($data),

            default => Log::debug("Unhandled webhook event: {$event}"),
        };

        return response()->json(['status' => 'ok']);
    }

    private function handleDelivered(array $data): void
    {
        // Update delivery stats in Laravel DB
        // Notify user if they have delivery notifications enabled
    }

    private function handleBounced(array $data): void
    {
        // Record bounce in Laravel DB
        // If hard bounce: disable the recipient, notify sender
        // If soft bounce: increment retry counter
    }

    private function handleTrackingOpen(array $data): void
    {
        // Update campaign analytics in Laravel DB
        // Broadcast to dashboard via WebSocket
        event(new TrackingEvent('open', $data));
    }

    private function handleTrackingClick(array $data): void
    {
        event(new TrackingEvent('click', $data));
    }

    private function handleQuotaWarning(array $data): void
    {
        // Send notification to user via email/Slack
        // Update billing dashboard
        event(new QuotaWarning($data));
    }

    private function handleAuthFailure(array $data): void
    {
        // Log security event
        // If threshold exceeded: lock account, notify admin
    }

    private function handleBruteForce(array $data): void
    {
        // Emergency: lock account, notify admin immediately
    }

    private function handleRateLimitExceeded(array $data): void
    {
        // Notify user, suggest plan upgrade
    }

    private function handleDeferred(array $data): void
    {
        // Log for monitoring, alert if queue grows
    }
}
```

**Webhook verification middleware:** `mailyte-api/app/Http/Middleware/VerifyMailyteWebhook.php`
```php
<?php

namespace App\Http\Middleware;

use Closure;
use Illuminate\Http\Request;

class VerifyMailyteWebhook
{
    public function handle(Request $request, Closure $next)
    {
        $signature = $request->header('X-Webhook-Signature');
        $secret = config('services.email_server.webhook_secret');

        if (!$signature || !$secret) {
            return $next($request); // Skip verification if not configured
        }

        $payload = $request->getContent();
        $expected = hash_hmac('sha256', $payload, $secret);

        if (!hash_equals($expected, $signature)) {
            return response()->json(['error' => 'Invalid webhook signature'], 403);
        }

        return $next($request);
    }
}
```

---

### Step 4: Configure Email Server Webhook Endpoint (30 min)

Set the Laravel webhook URL in the email server's `.env`:

```bash
# In mailyte-email-server/.env
WEBHOOK_URLS=http://mailyte-api:8000/api/webhooks/mailyte
WEBHOOK_SECRET=your-shared-secret
```

Or create a webhook endpoint via API:
```bash
curl -X POST http://localhost:8083/api/v1/webhooks/endpoints \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "http://mailyte-api:8000/api/webhooks/mailyte",
    "events": ["*"],
    "secret": "your-shared-secret"
  }'
```

---

### Step 5: Shared Authentication (1 day)

**Option A: Per-organization API keys (Simplest)**

When a user creates an organization in Laravel, generate an email server API key:

```php
// During org creation in Laravel:
$emailServer = app(EmailServerClient::class);
$keyResult = $emailServer->createApiKey($org->email_server_org_id, [
    'name' => "Laravel integration for {$org->name}",
    'permissions' => ['read', 'write'],
]);
$org->update(['email_server_api_key' => $keyResult['key_id']]);
```

Then use that per-org key for subsequent requests.

**Option B: Service-to-service token (Current approach)**

A single API key shared between Laravel and the email server. Simple, works for single-deployment setups.

---

### Step 6: Database Migration in Laravel (1 day)

Add columns to Laravel's tables to track email server resource IDs:

```php
// Migration
Schema::table('organizations', function (Blueprint $table) {
    $table->string('email_server_org_id')->nullable()->after('id');
});

Schema::table('domains', function (Blueprint $table) {
    $table->integer('email_server_domain_id')->nullable()->after('id');
    $table->json('dns_records')->nullable();
    $table->string('dkim_record')->nullable();
    $table->boolean('dns_verified')->default(false);
    $table->json('dns_verification_result')->nullable();
});

Schema::table('mailboxes', function (Blueprint $table) {
    $table->integer('email_server_account_id')->nullable()->after('id');
});
```

---

## Data Flow Summary

### User adds a domain

```
User → Frontend → Laravel API → Email Server API
                                      │
                                      ├→ Creates domain in MySQL
                                      ├→ Generates DKIM keys
                                      ├→ Returns DNS records
                                      │
                  Laravel API ←────────┘
                       │
                       ├→ Stores DNS records
                       ├→ Shows DNS setup instructions to user
                       │
User configures DNS ──→ Frontend → Laravel API → Email Server API
                                                       │
                                                       ├→ dig MX, SPF, DKIM, DMARC
                                                       ├→ Returns verification results
                                                       │
                                   Laravel API ←───────┘
                                        │
                                        └→ domain.dns_verified = true
```

### Email is sent and tracked

```
Sender → Postfix (587) → Rspamd → Tracking Filter → Delivery
                                                        │
                                                        ├→ Webhook: email.delivered
                                                        │     ↓
                                                        │  Webhooks service
                                                        │     ↓
                                                        │  POST to Laravel webhook URL
                                                        │     ↓
                                                        │  Laravel updates delivery stats
                                                        │
Recipient opens email ──→ Tracking pixel loaded
                              ↓
                         Tracking service records open
                              ↓
                         Webhook: tracking.open
                              ↓
                         POST to Laravel → update campaign stats
```

---

## Implementation Checklist

- [ ] Create `EmailServerClient` service class in Laravel
- [ ] Add `email_server_*_id` columns to Laravel tables
- [ ] Wire organization controller → email server provisioning
- [ ] Wire domain controller → email server + DNS records
- [ ] Wire mailbox controller → email server provisioning
- [ ] Create webhook receiver controller in Laravel
- [ ] Create webhook signature verification middleware
- [ ] Configure email server webhook URL
- [ ] Add Laravel queue jobs for async email server calls
- [ ] Add retry logic for failed email server API calls
- [ ] Add health check: Laravel → email server connectivity
- [ ] Write integration tests for the full flow

**Estimated effort: 3-5 days**
