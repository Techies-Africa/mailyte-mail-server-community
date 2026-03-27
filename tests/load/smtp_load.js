// k6 SMTP load test using k6/net/smtp extension
// Tests concurrent email sending through Postfix
// Target: 100K emails/day = ~70/minute sustained

import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';
import http from 'k6/http';

// Custom metrics
const emailsSent = new Counter('emails_sent');
const emailsFailed = new Counter('emails_failed');
const sendDuration = new Trend('smtp_send_duration', true);
const successRate = new Rate('smtp_success_rate');

export const options = {
    scenarios: {
        // Sustained sending load
        sustained: {
            executor: 'constant-arrival-rate',
            rate: 70,
            timeUnit: '1m',
            duration: '10m',
            preAllocatedVUs: 20,
            maxVUs: 50,
        },
        // Burst test
        burst: {
            executor: 'ramping-arrival-rate',
            startRate: 10,
            timeUnit: '1m',
            stages: [
                { duration: '2m', target: 100 },
                { duration: '3m', target: 200 },
                { duration: '2m', target: 50 },
                { duration: '1m', target: 0 },
            ],
            preAllocatedVUs: 30,
            maxVUs: 100,
            startTime: '12m',
        },
    },
    thresholds: {
        'smtp_success_rate': ['rate>0.95'],
        'smtp_send_duration': ['p(95)<5000', 'p(99)<10000'],
        'emails_failed': ['count<50'],
    },
};

const BASE_URL = __ENV.API_URL || 'http://localhost:8083';
const SMTP_HOST = __ENV.SMTP_HOST || 'localhost';
const SMTP_PORT = __ENV.SMTP_PORT || '587';
const TEST_DOMAIN = __ENV.TEST_DOMAIN || 'test.mailyte.local';
const TEST_USER = __ENV.TEST_USER || `loadtest@${TEST_DOMAIN}`;
const TEST_PASS = __ENV.TEST_PASS || 'testpassword';

// Since k6 doesn't have native SMTP, test via the API queue endpoint
export default function () {
    const uniqueId = `${Date.now()}-${__VU}-${__ITER}`;

    const payload = JSON.stringify({
        from: TEST_USER,
        to: `recipient-${uniqueId}@${TEST_DOMAIN}`,
        subject: `Load Test ${uniqueId}`,
        body: `This is a load test email sent at ${new Date().toISOString()}. ID: ${uniqueId}`,
        headers: {
            'X-Load-Test': 'true',
            'X-Test-ID': uniqueId,
        },
    });

    const params = {
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${TEST_PASS}`,
        },
        timeout: '10s',
    };

    const startTime = Date.now();
    const res = http.post(`${BASE_URL}/api/v1/queue/enqueue`, payload, params);
    const duration = Date.now() - startTime;

    sendDuration.add(duration);

    const success = check(res, {
        'status is 200 or 202': (r) => r.status === 200 || r.status === 202,
        'response has message_id': (r) => {
            try {
                const body = JSON.parse(r.body);
                return body.message_id || body.queue_id;
            } catch {
                return false;
            }
        },
    });

    if (success) {
        emailsSent.add(1);
        successRate.add(1);
    } else {
        emailsFailed.add(1);
        successRate.add(0);
    }

    sleep(0.1);
}

export function handleSummary(data) {
    return {
        'stdout': textSummary(data, { indent: '  ', enableColors: true }),
        'tests/load/results/smtp_load_results.json': JSON.stringify(data, null, 2),
    };
}

function textSummary(data, opts) {
    // k6 built-in text summary
    return JSON.stringify(data.metrics, null, 2);
}
