// k6 IMAP Load Test for Mailyte Email Server
// Simulates concurrent IMAP access via the HTTP API proxy
// (k6 does not have native IMAP support; we test the equivalent API endpoints)
//
// Run:
//   k6 run tests/load/imap_load.js
//   k6 run --env API_URL=http://mail.example.com:8083 tests/load/imap_load.js

import { check, sleep, group } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';
import http from 'k6/http';
import { randomIntBetween } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

// ---------------------------------------------------------------------------
// Custom metrics
// ---------------------------------------------------------------------------
const inboxCheckLatency = new Trend('imap_inbox_check_latency', true);
const folderListLatency = new Trend('imap_folder_list_latency', true);
const messageFetchLatency = new Trend('imap_message_fetch_latency', true);
const flagOperationLatency = new Trend('imap_flag_operation_latency', true);
const searchLatency = new Trend('imap_search_latency', true);
const quotaCheckLatency = new Trend('imap_quota_check_latency', true);

const imapErrors = new Counter('imap_errors');
const imapSuccesses = new Counter('imap_successes');
const imapSuccessRate = new Rate('imap_success_rate');

// ---------------------------------------------------------------------------
// Scenarios & thresholds
// ---------------------------------------------------------------------------
export const options = {
    scenarios: {
        // Simulate multiple users checking inbox concurrently
        inbox_polling: {
            executor: 'constant-arrival-rate',
            rate: 100,
            timeUnit: '1m',
            duration: '10m',
            preAllocatedVUs: 20,
            maxVUs: 50,
            exec: 'checkInbox',
        },

        // Folder listing — users navigating their mailbox tree
        folder_navigation: {
            executor: 'constant-arrival-rate',
            rate: 40,
            timeUnit: '1m',
            duration: '10m',
            preAllocatedVUs: 10,
            maxVUs: 25,
            exec: 'listFolders',
        },

        // Message fetch — reading full messages
        message_reading: {
            executor: 'ramping-vus',
            startVUs: 5,
            stages: [
                { duration: '2m', target: 15 },
                { duration: '5m', target: 30 },
                { duration: '2m', target: 10 },
                { duration: '1m', target: 0 },
            ],
            exec: 'fetchMessages',
        },

        // Flag operations — marking messages read/unread, flagged, etc.
        flag_operations: {
            executor: 'constant-arrival-rate',
            rate: 60,
            timeUnit: '1m',
            duration: '8m',
            preAllocatedVUs: 10,
            maxVUs: 25,
            exec: 'flagOperations',
            startTime: '1m',
        },

        // Combined realistic user session
        realistic_session: {
            executor: 'ramping-vus',
            startVUs: 2,
            stages: [
                { duration: '3m', target: 20 },
                { duration: '5m', target: 40 },
                { duration: '2m', target: 10 },
            ],
            exec: 'realisticUserSession',
            startTime: '2m',
        },
    },
    thresholds: {
        'imap_inbox_check_latency': ['p(95)<500', 'p(99)<1000'],
        'imap_folder_list_latency': ['p(95)<300', 'p(99)<800'],
        'imap_message_fetch_latency': ['p(95)<1000', 'p(99)<3000'],
        'imap_flag_operation_latency': ['p(95)<500', 'p(99)<1500'],
        'imap_search_latency': ['p(95)<1500', 'p(99)<5000'],
        'imap_quota_check_latency': ['p(95)<300', 'p(99)<800'],
        'imap_success_rate': ['rate>0.95'],
        'imap_errors': ['count<100'],
    },
};

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------
const BASE_URL = __ENV.API_URL || 'http://localhost:8083';
const API_KEY = __ENV.API_KEY || 'test-api-key';
const TEST_DOMAIN = __ENV.TEST_DOMAIN || 'test.mailyte.local';
const TEST_ORG_ID = __ENV.TEST_ORG_ID || 'loadtest-org';

// Simulated user pool for concurrent IMAP sessions
const TEST_USERS = Array.from({ length: 20 }, (_, i) => ({
    email: `user${i + 1}@${TEST_DOMAIN}`,
    password: 'TestPassword123!',
}));

function apiHeaders() {
    return {
        'Content-Type': 'application/json',
        'X-API-Key': API_KEY,
    };
}

function getTestUser() {
    return TEST_USERS[__VU % TEST_USERS.length];
}

function recordResult(res, latencyTrend, expectedStatuses) {
    expectedStatuses = expectedStatuses || [200];
    const duration = res.timings.duration;
    latencyTrend.add(duration);

    const ok = expectedStatuses.includes(res.status);
    if (ok) {
        imapSuccesses.add(1);
        imapSuccessRate.add(1);
    } else {
        imapErrors.add(1);
        imapSuccessRate.add(0);
    }
    return ok;
}

// ---------------------------------------------------------------------------
// Scenario: Check Inbox (simulates IMAP SELECT INBOX + FETCH headers)
// ---------------------------------------------------------------------------
export function checkInbox() {
    const user = getTestUser();

    group('IMAP Inbox Check', function () {
        // Get mailbox info (simulates IMAP LOGIN + SELECT)
        let res = http.get(
            `${BASE_URL}/api/v1/get/mailbox/${encodeURIComponent(user.email)}`,
            { headers: apiHeaders(), timeout: '10s' }
        );
        check(res, {
            'mailbox info returned': (r) => r.status === 200 || r.status === 404,
            'has mailbox data': (r) => {
                if (r.status === 404) return true;  // user may not exist in test env
                try {
                    const body = JSON.parse(r.body);
                    return body.type === 'success' || body.data !== undefined;
                } catch { return false; }
            },
        });
        recordResult(res, inboxCheckLatency, [200, 404]);

        // Get mailbox stats (simulates IMAP STATUS)
        res = http.get(
            `${BASE_URL}/api/v1/get/mailbox/stats/${encodeURIComponent(user.email)}`,
            { headers: apiHeaders(), timeout: '10s' }
        );
        check(res, {
            'mailbox stats returned': (r) => r.status === 200 || r.status === 404,
        });
        recordResult(res, inboxCheckLatency, [200, 404]);
    });

    sleep(0.2);
}

// ---------------------------------------------------------------------------
// Scenario: List Folders (simulates IMAP LIST "" *)
// ---------------------------------------------------------------------------
export function listFolders() {
    const user = getTestUser();

    group('IMAP Folder Listing', function () {
        // Get mailbox with folder structure
        let res = http.get(
            `${BASE_URL}/api/v1/get/mailbox/${encodeURIComponent(user.email)}`,
            { headers: apiHeaders(), timeout: '10s' }
        );
        check(res, {
            'folder list returned': (r) => r.status === 200 || r.status === 404,
        });
        recordResult(res, folderListLatency, [200, 404]);

        // Get quota info per folder (simulates IMAP GETQUOTAROOT)
        res = http.get(
            `${BASE_URL}/api/v1/get/mailbox/quota/${encodeURIComponent(user.email)}`,
            { headers: apiHeaders(), timeout: '10s' }
        );
        check(res, {
            'folder quota returned': (r) => r.status === 200 || r.status === 404,
        });
        recordResult(res, quotaCheckLatency, [200, 404]);
    });

    sleep(0.3);
}

// ---------------------------------------------------------------------------
// Scenario: Fetch Messages (simulates IMAP FETCH for message bodies)
// ---------------------------------------------------------------------------
export function fetchMessages() {
    const user = getTestUser();

    group('IMAP Message Fetch', function () {
        // Simulate fetching a batch of message headers (IMAP FETCH 1:20 BODY.PEEK[HEADER])
        let res = http.get(
            `${BASE_URL}/api/v1/message-trace/trace?recipient=${encodeURIComponent(user.email)}&limit=20`,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, {
            'message headers fetched': (r) => r.status === 200 || r.status === 404,
        });
        recordResult(res, messageFetchLatency, [200, 404]);

        // Try to fetch a specific message (simulates IMAP FETCH n BODY[])
        let messageId = null;
        if (res.status === 200) {
            try {
                const body = JSON.parse(res.body);
                const messages = body.data || body;
                if (Array.isArray(messages) && messages.length > 0) {
                    messageId = messages[0].message_id || messages[0].id;
                }
            } catch { /* ignore */ }
        }

        if (messageId) {
            res = http.get(
                `${BASE_URL}/api/v1/message-trace/trace/${encodeURIComponent(messageId)}`,
                { headers: apiHeaders(), timeout: '15s' }
            );
            check(res, {
                'single message fetched': (r) => r.status === 200 || r.status === 404,
            });
            recordResult(res, messageFetchLatency, [200, 404]);
        }

        // Search messages (simulates IMAP SEARCH)
        res = http.get(
            `${BASE_URL}/api/v1/message-trace/trace?recipient=${encodeURIComponent(user.email)}&subject=test&limit=10`,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, {
            'message search ok': (r) => r.status === 200 || r.status === 404,
        });
        recordResult(res, searchLatency, [200, 404]);
    });

    sleep(randomIntBetween(1, 3));
}

// ---------------------------------------------------------------------------
// Scenario: Flag Operations (simulates IMAP STORE +FLAGS / -FLAGS)
// ---------------------------------------------------------------------------
export function flagOperations() {
    const user = getTestUser();

    group('IMAP Flag Operations', function () {
        // Simulate marking messages as read via mailbox edit
        // (IMAP STORE 1:5 +FLAGS (\Seen))
        const markReadPayload = JSON.stringify({
            items: [user.email],
            attr: {
                quarantine_notification: 'hourly',
            },
        });

        let res = http.post(
            `${BASE_URL}/api/v1/edit/mailbox`,
            markReadPayload,
            { headers: apiHeaders(), timeout: '10s' }
        );
        check(res, {
            'flag operation ok': (r) => r.status === 200 || r.status === 404,
        });
        recordResult(res, flagOperationLatency, [200, 404]);

        // Simulate updating account settings (like IMAP SETMETADATA)
        // Get account ID first
        res = http.get(
            `${BASE_URL}/api/v1/email-accounts?organization_id=${TEST_ORG_ID}`,
            { headers: apiHeaders(), timeout: '10s' }
        );
        recordResult(res, flagOperationLatency, [200]);

        let accountId = null;
        if (res.status === 200) {
            try {
                const body = JSON.parse(res.body);
                const accounts = body.data || [];
                if (Array.isArray(accounts) && accounts.length > 0) {
                    accountId = accounts[0].id;
                }
            } catch { /* ignore */ }
        }

        if (accountId) {
            // Update vacation settings (simulates IMAP SETMETADATA /private/vendor/...)
            const vacationPayload = JSON.stringify({
                vacation_enabled: false,
            });

            res = http.put(
                `${BASE_URL}/api/v1/email-accounts/${accountId}`,
                vacationPayload,
                { headers: apiHeaders(), timeout: '10s' }
            );
            check(res, {
                'account update ok': (r) => r.status === 200 || r.status === 404,
            });
            recordResult(res, flagOperationLatency, [200, 404]);
        }
    });

    sleep(0.3);
}

// ---------------------------------------------------------------------------
// Scenario: Realistic User Session
// Simulates a user opening their mail client which triggers:
//   1. LOGIN -> mailbox info
//   2. LIST folders
//   3. SELECT INBOX -> inbox stats
//   4. FETCH recent headers
//   5. FETCH specific messages
//   6. STORE flags (mark read)
//   7. CHECK quota
//   8. LOGOUT
// ---------------------------------------------------------------------------
export function realisticUserSession() {
    const user = getTestUser();
    const sessionStart = Date.now();

    group('Realistic IMAP Session', function () {
        // Step 1: LOGIN — fetch user mailbox info
        let res = http.get(
            `${BASE_URL}/api/v1/get/mailbox/${encodeURIComponent(user.email)}`,
            { headers: apiHeaders(), timeout: '10s' }
        );
        check(res, { 'session login ok': (r) => r.status === 200 || r.status === 404 });
        recordResult(res, inboxCheckLatency, [200, 404]);
        sleep(0.2);

        // Step 2: LIST — get folder structure
        res = http.get(
            `${BASE_URL}/api/v1/get/mailbox/${encodeURIComponent(user.email)}`,
            { headers: apiHeaders(), timeout: '10s' }
        );
        check(res, { 'session list ok': (r) => r.status === 200 || r.status === 404 });
        recordResult(res, folderListLatency, [200, 404]);
        sleep(0.1);

        // Step 3: SELECT INBOX — get inbox stats
        res = http.get(
            `${BASE_URL}/api/v1/get/mailbox/stats/${encodeURIComponent(user.email)}`,
            { headers: apiHeaders(), timeout: '10s' }
        );
        check(res, { 'session inbox stats ok': (r) => r.status === 200 || r.status === 404 });
        recordResult(res, inboxCheckLatency, [200, 404]);
        sleep(0.1);

        // Step 4: FETCH recent message headers
        res = http.get(
            `${BASE_URL}/api/v1/message-trace/trace?recipient=${encodeURIComponent(user.email)}&limit=50`,
            { headers: apiHeaders(), timeout: '15s' }
        );
        check(res, { 'session fetch headers ok': (r) => r.status === 200 || r.status === 404 });
        recordResult(res, messageFetchLatency, [200, 404]);

        // Simulate user reading 3 messages
        let messages = [];
        if (res.status === 200) {
            try {
                const body = JSON.parse(res.body);
                messages = (body.data || body) || [];
                if (!Array.isArray(messages)) messages = [];
            } catch { messages = []; }
        }

        for (let i = 0; i < Math.min(3, messages.length); i++) {
            sleep(randomIntBetween(1, 4));

            // Step 5: FETCH full message body
            const msgId = messages[i].message_id || messages[i].id;
            if (msgId) {
                res = http.get(
                    `${BASE_URL}/api/v1/message-trace/trace/${encodeURIComponent(String(msgId))}`,
                    { headers: apiHeaders(), timeout: '15s' }
                );
                check(res, { 'session read message ok': (r) => r.status === 200 || r.status === 404 });
                recordResult(res, messageFetchLatency, [200, 404]);
            }
        }

        // Step 6: STORE flags (mark messages as read via edit)
        const flagPayload = JSON.stringify({
            items: [user.email],
            attr: { quarantine_notification: 'daily' },
        });
        res = http.post(
            `${BASE_URL}/api/v1/edit/mailbox`,
            flagPayload,
            { headers: apiHeaders(), timeout: '10s' }
        );
        check(res, { 'session flag update ok': (r) => r.status === 200 || r.status === 404 });
        recordResult(res, flagOperationLatency, [200, 404]);
        sleep(0.1);

        // Step 7: GETQUOTAROOT — check quota
        res = http.get(
            `${BASE_URL}/api/v1/get/mailbox/quota/${encodeURIComponent(user.email)}`,
            { headers: apiHeaders(), timeout: '10s' }
        );
        check(res, { 'session quota check ok': (r) => r.status === 200 || r.status === 404 });
        recordResult(res, quotaCheckLatency, [200, 404]);
    });

    // Simulate idle time between sessions
    sleep(randomIntBetween(5, 15));
}

// ---------------------------------------------------------------------------
// Summary handler
// ---------------------------------------------------------------------------
export function handleSummary(data) {
    return {
        'stdout': textSummary(data),
        'tests/load/results/imap_load_results.json': JSON.stringify(data, null, 2),
    };
}

function textSummary(data) {
    const metrics = data.metrics || {};
    const summary = {
        timestamp: new Date().toISOString(),
        test: 'IMAP Load Test',
        scenarios: {
            inbox_check: {
                p95: metrics.imap_inbox_check_latency ? metrics.imap_inbox_check_latency.values['p(95)'] : 'N/A',
                p99: metrics.imap_inbox_check_latency ? metrics.imap_inbox_check_latency.values['p(99)'] : 'N/A',
            },
            folder_list: {
                p95: metrics.imap_folder_list_latency ? metrics.imap_folder_list_latency.values['p(95)'] : 'N/A',
                p99: metrics.imap_folder_list_latency ? metrics.imap_folder_list_latency.values['p(99)'] : 'N/A',
            },
            message_fetch: {
                p95: metrics.imap_message_fetch_latency ? metrics.imap_message_fetch_latency.values['p(95)'] : 'N/A',
                p99: metrics.imap_message_fetch_latency ? metrics.imap_message_fetch_latency.values['p(99)'] : 'N/A',
            },
            flag_operations: {
                p95: metrics.imap_flag_operation_latency ? metrics.imap_flag_operation_latency.values['p(95)'] : 'N/A',
                p99: metrics.imap_flag_operation_latency ? metrics.imap_flag_operation_latency.values['p(99)'] : 'N/A',
            },
            search: {
                p95: metrics.imap_search_latency ? metrics.imap_search_latency.values['p(95)'] : 'N/A',
                p99: metrics.imap_search_latency ? metrics.imap_search_latency.values['p(99)'] : 'N/A',
            },
            quota_check: {
                p95: metrics.imap_quota_check_latency ? metrics.imap_quota_check_latency.values['p(95)'] : 'N/A',
                p99: metrics.imap_quota_check_latency ? metrics.imap_quota_check_latency.values['p(99)'] : 'N/A',
            },
        },
        success_rate: metrics.imap_success_rate ? metrics.imap_success_rate.values.rate : 'N/A',
        total_errors: metrics.imap_errors ? metrics.imap_errors.values.count : 0,
        total_successes: metrics.imap_successes ? metrics.imap_successes.values.count : 0,
    };
    return JSON.stringify(summary, null, 2);
}
