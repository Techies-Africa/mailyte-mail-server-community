// k6 API Load Test for Mailyte Email Server
// Tests all major API endpoints under concurrent load
//
// Run:
//   k6 run tests/load/api_load.js
//   k6 run --env API_URL=http://mail.example.com:8083 --env API_KEY=your-key tests/load/api_load.js

import { check, sleep, group } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';
import http from 'k6/http';
import { randomString, randomIntBetween } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

// ---------------------------------------------------------------------------
// Custom metrics
// ---------------------------------------------------------------------------
const readLatency = new Trend('api_read_latency', true);
const writeLatency = new Trend('api_write_latency', true);
const healthLatency = new Trend('api_health_latency', true);
const searchLatency = new Trend('api_search_latency', true);
const analyticsLatency = new Trend('api_analytics_latency', true);
const webhookLatency = new Trend('api_webhook_latency', true);

const apiErrors = new Counter('api_errors');
const apiSuccesses = new Counter('api_successes');
const apiSuccessRate = new Rate('api_success_rate');

// ---------------------------------------------------------------------------
// Scenarios & thresholds
// ---------------------------------------------------------------------------
export const options = {
    scenarios: {
        // High-frequency health checks
        health_checks: {
            executor: 'constant-arrival-rate',
            rate: 200,
            timeUnit: '1m',
            duration: '10m',
            preAllocatedVUs: 5,
            maxVUs: 20,
            exec: 'healthCheck',
        },

        // Mailbox CRUD operations
        mailbox_crud: {
            executor: 'ramping-vus',
            startVUs: 2,
            stages: [
                { duration: '2m', target: 10 },
                { duration: '5m', target: 20 },
                { duration: '2m', target: 5 },
                { duration: '1m', target: 0 },
            ],
            exec: 'mailboxCrud',
            startTime: '0s',
        },

        // Email search / query
        email_search: {
            executor: 'constant-arrival-rate',
            rate: 50,
            timeUnit: '1m',
            duration: '8m',
            preAllocatedVUs: 10,
            maxVUs: 30,
            exec: 'emailSearch',
            startTime: '1m',
        },

        // Analytics endpoints
        analytics_queries: {
            executor: 'constant-arrival-rate',
            rate: 30,
            timeUnit: '1m',
            duration: '8m',
            preAllocatedVUs: 5,
            maxVUs: 15,
            exec: 'analyticsQueries',
            startTime: '1m',
        },

        // Webhook delivery simulation
        webhook_delivery: {
            executor: 'ramping-arrival-rate',
            startRate: 5,
            timeUnit: '1m',
            stages: [
                { duration: '2m', target: 30 },
                { duration: '4m', target: 60 },
                { duration: '2m', target: 10 },
                { duration: '1m', target: 0 },
            ],
            preAllocatedVUs: 10,
            maxVUs: 30,
            exec: 'webhookDelivery',
            startTime: '2m',
        },
    },
    thresholds: {
        // Read endpoints: p95 < 500ms
        'api_read_latency': ['p(95)<500', 'p(99)<1000'],
        // Write endpoints: p95 < 2000ms
        'api_write_latency': ['p(95)<2000', 'p(99)<5000'],
        // Health checks: p95 < 200ms
        'api_health_latency': ['p(95)<200', 'p(99)<500'],
        // Search: p95 < 1000ms
        'api_search_latency': ['p(95)<1000', 'p(99)<3000'],
        // Analytics: p95 < 1500ms
        'api_analytics_latency': ['p(95)<1500', 'p(99)<3000'],
        // Error rate < 1%
        'api_success_rate': ['rate>0.99'],
        // Total errors capped
        'api_errors': ['count<100'],
    },
};

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------
const BASE_URL = __ENV.API_URL || 'http://localhost:8083';
const API_KEY = __ENV.API_KEY || 'test-api-key';
const TEST_DOMAIN = __ENV.TEST_DOMAIN || 'test.mailyte.local';
const TEST_ORG_ID = __ENV.TEST_ORG_ID || 'loadtest-org';

function apiHeaders(extra) {
    return Object.assign({
        'Content-Type': 'application/json',
        'X-API-Key': API_KEY,
    }, extra || {});
}

function recordResult(res, latencyTrend, expectedStatuses) {
    expectedStatuses = expectedStatuses || [200, 201, 202];
    const duration = res.timings.duration;
    latencyTrend.add(duration);

    const ok = expectedStatuses.includes(res.status);
    if (ok) {
        apiSuccesses.add(1);
        apiSuccessRate.add(1);
    } else {
        apiErrors.add(1);
        apiSuccessRate.add(0);
    }
    return ok;
}

// ---------------------------------------------------------------------------
// Scenario: Health Checks
// ---------------------------------------------------------------------------
export function healthCheck() {
    group('Health Check', function () {
        // Root endpoint
        let res = http.get(`${BASE_URL}/`, { headers: apiHeaders(), timeout: '5s' });
        check(res, { 'root returns 200': (r) => r.status === 200 });
        recordResult(res, healthLatency);

        // Health endpoint
        res = http.get(`${BASE_URL}/health`, { headers: apiHeaders(), timeout: '5s' });
        check(res, {
            'health returns 200': (r) => r.status === 200,
            'health status is healthy': (r) => {
                try {
                    return JSON.parse(r.body).status === 'healthy';
                } catch { return false; }
            },
        });
        recordResult(res, healthLatency);
    });

    sleep(0.1);
}

// ---------------------------------------------------------------------------
// Scenario: Mailbox CRUD
// ---------------------------------------------------------------------------
export function mailboxCrud() {
    const suffix = `${__VU}-${__ITER}-${Date.now()}`;
    const localPart = `loadtest-${suffix}`;
    const emailAddr = `${localPart}@${TEST_DOMAIN}`;
    let accountId = null;

    group('Mailbox CRUD', function () {
        // List existing mailboxes
        let res = http.get(
            `${BASE_URL}/api/v1/email-accounts?organization_id=${TEST_ORG_ID}`,
            { headers: apiHeaders(), timeout: '10s' }
        );
        check(res, { 'list mailboxes ok': (r) => r.status === 200 });
        recordResult(res, readLatency);

        // Create mailbox
        const createPayload = JSON.stringify({
            email: emailAddr,
            password: 'LoadTest1234!',
            name: `Load Test User ${suffix}`,
            storage_quota: 1073741824,
        });

        res = http.post(
            `${BASE_URL}/api/v1/email-accounts`,
            createPayload,
            { headers: apiHeaders(), timeout: '10s' }
        );
        const created = check(res, {
            'create mailbox status': (r) => r.status === 201 || r.status === 200,
        });
        recordResult(res, writeLatency, [200, 201]);

        if (created) {
            try {
                const body = JSON.parse(res.body);
                accountId = (body.data && body.data.id) ? body.data.id : null;
            } catch { /* ignore parse errors */ }
        }

        // Get specific mailbox
        if (accountId) {
            res = http.get(
                `${BASE_URL}/api/v1/email-accounts/${accountId}`,
                { headers: apiHeaders(), timeout: '10s' }
            );
            check(res, { 'get mailbox ok': (r) => r.status === 200 });
            recordResult(res, readLatency);
        }

        // Update mailbox
        if (accountId) {
            const updatePayload = JSON.stringify({
                name: `Updated Load Test ${suffix}`,
                forward_enabled: true,
                forward_destination: `forward-${suffix}@${TEST_DOMAIN}`,
            });

            res = http.put(
                `${BASE_URL}/api/v1/email-accounts/${accountId}`,
                updatePayload,
                { headers: apiHeaders(), timeout: '10s' }
            );
            check(res, { 'update mailbox ok': (r) => r.status === 200 });
            recordResult(res, writeLatency);
        }

        // Get mailbox quotas
        if (accountId) {
            res = http.get(
                `${BASE_URL}/api/v1/email-accounts/${accountId}/quotas`,
                { headers: apiHeaders(), timeout: '10s' }
            );
            check(res, { 'get mailbox quotas ok': (r) => r.status === 200 });
            recordResult(res, readLatency);
        }

        // Delete mailbox (cleanup)
        if (accountId) {
            res = http.del(
                `${BASE_URL}/api/v1/email-accounts/${accountId}`,
                null,
                { headers: apiHeaders(), timeout: '10s' }
            );
            check(res, { 'delete mailbox ok': (r) => r.status === 200 });
            recordResult(res, writeLatency);
        }
    });

    sleep(randomIntBetween(1, 3));
}

// ---------------------------------------------------------------------------
// Scenario: Email Search / Query
// ---------------------------------------------------------------------------
export function emailSearch() {
    group('Email Search & Query', function () {
        // Message trace search
        let res = http.get(
            `${BASE_URL}/api/v1/message-trace/trace?organization_id=${TEST_ORG_ID}&limit=20`,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, {
            'message trace ok': (r) => r.status === 200 || r.status === 404,
        });
        recordResult(res, searchLatency, [200, 404]);

        // Search by sender
        res = http.get(
            `${BASE_URL}/api/v1/message-trace/trace?sender=loadtest@${TEST_DOMAIN}&limit=10`,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, {
            'search by sender ok': (r) => r.status === 200 || r.status === 404,
        });
        recordResult(res, searchLatency, [200, 404]);

        // Search by recipient
        res = http.get(
            `${BASE_URL}/api/v1/message-trace/trace?recipient=admin@${TEST_DOMAIN}&limit=10`,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, {
            'search by recipient ok': (r) => r.status === 200 || r.status === 404,
        });
        recordResult(res, searchLatency, [200, 404]);

        // Search with date range
        const now = new Date();
        const yesterday = new Date(now.getTime() - 86400000);
        res = http.get(
            `${BASE_URL}/api/v1/message-trace/trace?start_date=${yesterday.toISOString()}&end_date=${now.toISOString()}&limit=25`,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, {
            'date-range search ok': (r) => r.status === 200 || r.status === 404,
        });
        recordResult(res, searchLatency, [200, 404]);

        // Quarantine list
        res = http.get(
            `${BASE_URL}/api/v1/message-trace/quarantine?organization_id=${TEST_ORG_ID}&limit=20`,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, {
            'quarantine list ok': (r) => r.status === 200 || r.status === 404,
        });
        recordResult(res, searchLatency, [200, 404]);

        // Audit logs
        res = http.get(
            `${BASE_URL}/api/v1/message-trace/audit?organization_id=${TEST_ORG_ID}&limit=20`,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, {
            'audit logs ok': (r) => r.status === 200 || r.status === 404,
        });
        recordResult(res, searchLatency, [200, 404]);
    });

    sleep(0.5);
}

// ---------------------------------------------------------------------------
// Scenario: Analytics Queries
// ---------------------------------------------------------------------------
export function analyticsQueries() {
    group('Analytics Queries', function () {
        // Dashboard data
        let res = http.get(
            `${BASE_URL}/api/v1/analytics/dashboard/${TEST_DOMAIN}`,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, {
            'analytics dashboard ok': (r) => [200, 503].includes(r.status),
        });
        recordResult(res, analyticsLatency, [200, 503]);

        // Email volume
        res = http.get(
            `${BASE_URL}/api/v1/analytics/email-volume/${TEST_DOMAIN}?period=7d`,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, {
            'email volume ok': (r) => [200, 503].includes(r.status),
        });
        recordResult(res, analyticsLatency, [200, 503]);

        // Engagement metrics
        res = http.get(
            `${BASE_URL}/api/v1/analytics/engagement/${TEST_DOMAIN}`,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, {
            'engagement metrics ok': (r) => [200, 503].includes(r.status),
        });
        recordResult(res, analyticsLatency, [200, 503]);

        // Deliverability metrics
        res = http.get(
            `${BASE_URL}/api/v1/analytics/deliverability/${TEST_DOMAIN}`,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, {
            'deliverability metrics ok': (r) => [200, 503].includes(r.status),
        });
        recordResult(res, analyticsLatency, [200, 503]);

        // Domain metrics
        res = http.get(
            `${BASE_URL}/api/v1/analytics/metrics/${TEST_DOMAIN}`,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, {
            'domain metrics ok': (r) => [200, 503].includes(r.status),
        });
        recordResult(res, analyticsLatency, [200, 503]);

        // Tracking stats for domain
        res = http.get(
            `${BASE_URL}/api/v1/tracking/stats/domain/${TEST_DOMAIN}`,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, {
            'tracking stats ok': (r) => [200, 503].includes(r.status),
        });
        recordResult(res, analyticsLatency, [200, 503]);

        // Storage usage
        res = http.get(
            `${BASE_URL}/api/v1/storage/usage/domain/${TEST_DOMAIN}`,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, {
            'storage usage ok': (r) => [200, 503].includes(r.status),
        });
        recordResult(res, analyticsLatency, [200, 503]);

        // Rate limiter usage
        res = http.get(
            `${BASE_URL}/api/v1/rate-limiter/rate-limits/usage/${TEST_DOMAIN}`,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, {
            'rate limit usage ok': (r) => [200, 503].includes(r.status),
        });
        recordResult(res, analyticsLatency, [200, 503]);
    });

    sleep(1);
}

// ---------------------------------------------------------------------------
// Scenario: Webhook Delivery Simulation
// ---------------------------------------------------------------------------
export function webhookDelivery() {
    const uniqueId = `${Date.now()}-${__VU}-${__ITER}`;

    group('Webhook Operations', function () {
        // List webhook subscriptions
        let res = http.get(
            `${BASE_URL}/api/v1/webhooks/subscriptions?domain=${TEST_DOMAIN}`,
            { headers: apiHeaders(), timeout: '10s' }
        );
        check(res, {
            'list subscriptions ok': (r) => [200, 503].includes(r.status),
        });
        recordResult(res, webhookLatency, [200, 503]);

        // Create webhook subscription
        const createPayload = JSON.stringify({
            url: `https://webhook.site/test-${uniqueId}`,
            events: ['email.sent', 'email.delivered', 'email.bounced', 'email.opened'],
            domain: TEST_DOMAIN,
            secret: `webhook-secret-${uniqueId}`,
            active: true,
        });

        res = http.post(
            `${BASE_URL}/api/v1/webhooks/subscriptions`,
            createPayload,
            { headers: apiHeaders(), timeout: '10s' }
        );
        check(res, {
            'create subscription ok': (r) => [200, 201, 503].includes(r.status),
        });
        recordResult(res, webhookLatency, [200, 201, 503]);

        let subscriptionId = null;
        try {
            const body = JSON.parse(res.body);
            subscriptionId = body.id || body.subscription_id || (body.data && body.data.id);
        } catch { /* ignore */ }

        // Test webhook delivery
        const testPayload = JSON.stringify({
            url: `https://webhook.site/test-${uniqueId}`,
            event_type: 'email.sent',
            payload: {
                message_id: `msg-${uniqueId}`,
                from: `loadtest@${TEST_DOMAIN}`,
                to: `recipient@${TEST_DOMAIN}`,
                subject: 'Webhook test',
                timestamp: new Date().toISOString(),
            },
        });

        res = http.post(
            `${BASE_URL}/api/v1/webhooks/test`,
            testPayload,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, {
            'test webhook ok': (r) => [200, 503].includes(r.status),
        });
        recordResult(res, webhookLatency, [200, 503]);

        // Get webhook events
        res = http.get(
            `${BASE_URL}/api/v1/webhooks/events/${TEST_DOMAIN}`,
            { headers: apiHeaders(), timeout: '10s' }
        );
        check(res, {
            'get events ok': (r) => [200, 503].includes(r.status),
        });
        recordResult(res, webhookLatency, [200, 503]);

        // Cleanup: delete subscription
        if (subscriptionId) {
            res = http.del(
                `${BASE_URL}/api/v1/webhooks/subscriptions/${subscriptionId}`,
                null,
                { headers: apiHeaders(), timeout: '10s' }
            );
            check(res, {
                'delete subscription ok': (r) => [200, 204, 503].includes(r.status),
            });
            recordResult(res, webhookLatency, [200, 204, 503]);
        }
    });

    sleep(0.5);
}

// ---------------------------------------------------------------------------
// Summary handler
// ---------------------------------------------------------------------------
export function handleSummary(data) {
    return {
        'stdout': textSummary(data),
        'tests/load/results/api_load_results.json': JSON.stringify(data, null, 2),
    };
}

function textSummary(data) {
    const metrics = data.metrics || {};
    const summary = {
        timestamp: new Date().toISOString(),
        scenarios: {
            health_checks: {
                p95: metrics.api_health_latency ? metrics.api_health_latency.values['p(95)'] : 'N/A',
                p99: metrics.api_health_latency ? metrics.api_health_latency.values['p(99)'] : 'N/A',
            },
            reads: {
                p95: metrics.api_read_latency ? metrics.api_read_latency.values['p(95)'] : 'N/A',
                p99: metrics.api_read_latency ? metrics.api_read_latency.values['p(99)'] : 'N/A',
            },
            writes: {
                p95: metrics.api_write_latency ? metrics.api_write_latency.values['p(95)'] : 'N/A',
                p99: metrics.api_write_latency ? metrics.api_write_latency.values['p(99)'] : 'N/A',
            },
            search: {
                p95: metrics.api_search_latency ? metrics.api_search_latency.values['p(95)'] : 'N/A',
                p99: metrics.api_search_latency ? metrics.api_search_latency.values['p(99)'] : 'N/A',
            },
            analytics: {
                p95: metrics.api_analytics_latency ? metrics.api_analytics_latency.values['p(95)'] : 'N/A',
                p99: metrics.api_analytics_latency ? metrics.api_analytics_latency.values['p(99)'] : 'N/A',
            },
            webhooks: {
                p95: metrics.api_webhook_latency ? metrics.api_webhook_latency.values['p(95)'] : 'N/A',
                p99: metrics.api_webhook_latency ? metrics.api_webhook_latency.values['p(99)'] : 'N/A',
            },
        },
        success_rate: metrics.api_success_rate ? metrics.api_success_rate.values.rate : 'N/A',
        total_errors: metrics.api_errors ? metrics.api_errors.values.count : 0,
        total_successes: metrics.api_successes ? metrics.api_successes.values.count : 0,
    };
    return JSON.stringify(summary, null, 2);
}
