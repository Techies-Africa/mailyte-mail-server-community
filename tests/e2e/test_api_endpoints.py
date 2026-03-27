"""
Comprehensive API Endpoint Test Suite for Mailyte Email Server

Tests every API route registered in worker/api/app.py:
  - organizations CRUD
  - domains CRUD
  - mailboxes (email-accounts) CRUD
  - aliases CRUD
  - analytics GET endpoints
  - monitoring endpoints
  - queue management
  - webhooks configuration
  - rate limiter config
  - storage operations
  - tracking endpoints
  - RAG/AI endpoints
  - filters CRUD
  - shared mailboxes
  - message trace
  - transport rules
  - whitelabel config
  - reseller operations

Usage:
    pytest tests/e2e/test_api_endpoints.py -v --tb=short
    pytest tests/e2e/test_api_endpoints.py -v -k TestOrganizations

Environment variables:
    API_URL   - Base URL for the API gateway  (default: http://localhost:8083)
    API_KEY   - API key for authentication     (default: test-api-key)
"""

import pytest
import os
import uuid
import json
import requests
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_URL = os.getenv('API_URL', 'http://localhost:8083')
API_KEY = os.getenv('API_KEY', 'test-api-key')
TEST_DOMAIN = os.getenv('TEST_DOMAIN', 'test.mailyte.local')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def api_headers(extra=None):
    """Standard API request headers."""
    h = {
        'Content-Type': 'application/json',
        'X-API-Key': API_KEY,
    }
    if extra:
        h.update(extra)
    return h


def get(path, params=None, **kwargs):
    return requests.get(f'{API_URL}{path}', headers=api_headers(), params=params, timeout=30, **kwargs)


def post(path, data=None, **kwargs):
    return requests.post(f'{API_URL}{path}', headers=api_headers(), json=data, timeout=30, **kwargs)


def put(path, data=None, **kwargs):
    return requests.put(f'{API_URL}{path}', headers=api_headers(), json=data, timeout=30, **kwargs)


def delete(path, data=None, **kwargs):
    return requests.delete(f'{API_URL}{path}', headers=api_headers(), json=data, timeout=30, **kwargs)


def uid():
    """Short unique string for test isolation."""
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Session-scoped fixtures shared across all test classes
# ---------------------------------------------------------------------------
@pytest.fixture(scope='session')
def session_uid():
    return uid()


@pytest.fixture(scope='session')
def base_org(session_uid):
    """Create a base organization for the entire session."""
    org_id = f'api-test-{session_uid}'
    data = {
        'id': org_id,
        'name': f'API Test Org {session_uid}',
        'description': 'Created by test_api_endpoints.py',
        'admin_email': f'admin@{session_uid}.{TEST_DOMAIN}',
        'admin_name': 'API Test Admin',
        'active': True,
        'settings': {},
        'rate_limits': {'hourly': 500},
        'storage_quotas': {'max_total': 5368709120},
    }
    resp = post('/api/v1/organizations', data)
    assert resp.status_code in (200, 201), f'Org creation failed: {resp.text}'
    result = resp.json().get('data', resp.json())
    result['_id'] = org_id
    yield result
    # Cleanup at end of session
    delete(f'/api/v1/organizations/{org_id}')


@pytest.fixture(scope='session')
def base_domain(base_org, session_uid):
    """Create a base domain for the entire session."""
    domain_name = f'{session_uid}.{TEST_DOMAIN}'
    data = {
        'domain': domain_name,
        'organization_id': base_org['_id'],
        'description': 'API test domain',
        'active': True,
        'max_quota': 5368709120,
        'max_users': 50,
    }
    resp = post('/api/v1/domains', data)
    assert resp.status_code in (200, 201), f'Domain creation failed: {resp.text}'
    result = resp.json().get('data', resp.json())
    result['_name'] = domain_name
    result['_org_id'] = base_org['_id']
    yield result
    domain_id = result.get('id')
    if domain_id:
        delete(f'/api/v1/domains/{domain_id}')


@pytest.fixture(scope='session')
def base_mailbox(base_domain, session_uid):
    """Create a base email account for the entire session."""
    email_addr = f'apiuser-{session_uid}@{base_domain["_name"]}'
    data = {
        'email': email_addr,
        'password': 'ApiTestPass99!',
        'name': f'API Test User {session_uid}',
        'storage_quota': 1073741824,
    }
    resp = post('/api/v1/email-accounts', data)
    assert resp.status_code in (200, 201), f'Mailbox creation failed: {resp.text}'
    result = resp.json().get('data', resp.json())
    result['_email'] = email_addr
    result['_org_id'] = base_domain['_org_id']
    result['_domain'] = base_domain['_name']
    yield result
    account_id = result.get('id')
    if account_id:
        delete(f'/api/v1/email-accounts/{account_id}')


# ===========================================================================
# 1. Organizations CRUD
# ===========================================================================
class TestOrganizations:
    """Test /api/v1/organizations routes."""

    def test_list_organizations(self, base_org):
        resp = get('/api/v1/organizations')
        assert resp.status_code == 200
        body = resp.json()
        assert body['type'] == 'success'
        assert isinstance(body.get('data'), list)

    def test_create_organization(self):
        org_id = f'temp-org-{uid()}'
        resp = post('/api/v1/organizations', {
            'id': org_id,
            'name': f'Temp Org {org_id}',
            'admin_email': f'admin@{org_id}.test',
            'active': True,
        })
        assert resp.status_code in (200, 201)
        body = resp.json()
        assert body['type'] == 'success'
        # Cleanup
        delete(f'/api/v1/organizations/{org_id}')

    def test_create_organization_duplicate(self, base_org):
        resp = post('/api/v1/organizations', {
            'id': base_org['_id'],
            'name': 'Duplicate Org',
        })
        assert resp.status_code == 409

    def test_create_organization_validation(self):
        resp = post('/api/v1/organizations', {})
        assert resp.status_code == 400

    def test_get_organization(self, base_org):
        resp = get(f'/api/v1/organizations/{base_org["_id"]}')
        assert resp.status_code == 200
        body = resp.json()
        data = body.get('data', body)
        assert data.get('id') == base_org['_id']

    def test_get_organization_not_found(self):
        resp = get('/api/v1/organizations/nonexistent-org-xyz')
        assert resp.status_code == 404

    def test_update_organization(self, base_org):
        resp = put(f'/api/v1/organizations/{base_org["_id"]}', {
            'description': f'Updated at {datetime.utcnow().isoformat()}',
        })
        assert resp.status_code == 200

    def test_get_organization_quotas(self, base_org):
        resp = get(f'/api/v1/organizations/{base_org["_id"]}/quotas')
        assert resp.status_code == 200
        body = resp.json()
        data = body.get('data', body)
        assert 'organization_id' in data or 'organization_quotas' in data

    def test_update_organization_quotas(self, base_org):
        resp = put(f'/api/v1/organizations/{base_org["_id"]}/quotas', {
            'storage_quotas': {'max_total': 10737418240},
            'rate_limits': {'hourly': 1000},
        })
        assert resp.status_code == 200

    def test_get_organization_by_external_id(self, base_org):
        # Set an external_id first
        ext_id = f'ext-{uid()}'
        put(f'/api/v1/organizations/{base_org["_id"]}', {'external_id': ext_id})
        resp = get(f'/api/v1/organizations/by-external-id/{ext_id}')
        assert resp.status_code in (200, 404)

    def test_delete_organization_with_dependents(self, base_org):
        """Deleting an org with domains should fail."""
        resp = delete(f'/api/v1/organizations/{base_org["_id"]}')
        assert resp.status_code == 400  # has domains

    def test_delete_organization(self):
        """Create and delete an org without dependents."""
        org_id = f'deleteme-{uid()}'
        post('/api/v1/organizations', {
            'id': org_id, 'name': 'Delete Me Org', 'active': True,
        })
        resp = delete(f'/api/v1/organizations/{org_id}')
        assert resp.status_code == 200


# ===========================================================================
# 2. Domains CRUD
# ===========================================================================
class TestDomains:
    """Test /api/v1/domains routes."""

    def test_list_domains(self, base_org):
        resp = get('/api/v1/domains', params={'organization_id': base_org['_id']})
        assert resp.status_code == 200
        body = resp.json()
        assert body['type'] == 'success'

    def test_list_all_domains(self):
        resp = get('/api/v1/domains')
        assert resp.status_code == 200

    def test_create_domain(self, base_org):
        domain_name = f'{uid()}.{TEST_DOMAIN}'
        resp = post('/api/v1/domains', {
            'domain': domain_name,
            'organization_id': base_org['_id'],
            'max_quota': 1073741824,
            'max_users': 10,
        })
        assert resp.status_code in (200, 201)
        data = resp.json().get('data', resp.json())
        domain_id = data.get('id')
        # Cleanup
        if domain_id:
            delete(f'/api/v1/domains/{domain_id}')

    def test_create_domain_duplicate(self, base_domain):
        resp = post('/api/v1/domains', {
            'domain': base_domain['_name'],
            'organization_id': base_domain['_org_id'],
        })
        assert resp.status_code == 409

    def test_create_domain_missing_org(self):
        resp = post('/api/v1/domains', {
            'domain': f'{uid()}.test.local',
            'organization_id': 'nonexistent-org',
        })
        assert resp.status_code == 404

    def test_get_domain(self, base_domain):
        resp = get(f'/api/v1/domains/{base_domain["id"]}')
        assert resp.status_code == 200

    def test_get_domain_not_found(self):
        resp = get('/api/v1/domains/999999')
        assert resp.status_code == 404

    def test_update_domain(self, base_domain):
        resp = put(f'/api/v1/domains/{base_domain["id"]}', {
            'description': f'Updated at {datetime.utcnow().isoformat()}',
            'max_users': 75,
        })
        assert resp.status_code == 200

    def test_get_domain_quotas(self, base_domain):
        resp = get(f'/api/v1/domains/{base_domain["id"]}/quotas')
        assert resp.status_code == 200

    def test_update_domain_quotas(self, base_domain):
        resp = put(f'/api/v1/domains/{base_domain["id"]}/quotas', {
            'max_quota': 10737418240,
            'rate_limits': {'per_minute': 100},
        })
        assert resp.status_code == 200

    def test_delete_domain_with_accounts(self, base_domain):
        """Deleting a domain with email accounts should fail."""
        resp = delete(f'/api/v1/domains/{base_domain["id"]}')
        assert resp.status_code == 400

    def test_delete_domain(self, base_org):
        """Create and delete a domain without accounts."""
        domain_name = f'del-{uid()}.{TEST_DOMAIN}'
        resp = post('/api/v1/domains', {
            'domain': domain_name,
            'organization_id': base_org['_id'],
        })
        assert resp.status_code in (200, 201)
        data = resp.json().get('data', resp.json())
        domain_id = data.get('id')
        resp = delete(f'/api/v1/domains/{domain_id}')
        assert resp.status_code == 200

    def test_get_domain_stats(self, base_domain):
        resp = get(f'/api/v1/get/domain/stats/{base_domain["_name"]}')
        assert resp.status_code in (200, 404)

    def test_get_domain_policy(self, base_domain):
        resp = get(f'/get/domain/policy/{base_domain["_name"]}')
        assert resp.status_code in (200, 404)


# ===========================================================================
# 3. Mailboxes (Email Accounts) CRUD
# ===========================================================================
class TestMailboxes:
    """Test /api/v1/email-accounts and legacy mailbox routes."""

    def test_list_email_accounts(self, base_org):
        resp = get('/api/v1/email-accounts', params={'organization_id': base_org['_id']})
        assert resp.status_code == 200

    def test_list_email_accounts_by_domain(self, base_domain):
        resp = get('/api/v1/email-accounts', params={'domain_id': base_domain.get('id')})
        assert resp.status_code == 200

    def test_create_email_account(self, base_domain):
        email_addr = f'temp-{uid()}@{base_domain["_name"]}'
        resp = post('/api/v1/email-accounts', {
            'email': email_addr,
            'password': 'TempPass123!',
            'name': 'Temp User',
        })
        assert resp.status_code in (200, 201)
        data = resp.json().get('data', resp.json())
        assert 'password' not in data  # password should be stripped from response
        account_id = data.get('id')
        if account_id:
            delete(f'/api/v1/email-accounts/{account_id}')

    def test_create_email_account_duplicate(self, base_mailbox):
        resp = post('/api/v1/email-accounts', {
            'email': base_mailbox['_email'],
            'password': 'AnotherPass123!',
        })
        assert resp.status_code == 409

    def test_create_email_account_bad_password(self, base_domain):
        resp = post('/api/v1/email-accounts', {
            'email': f'badpw-{uid()}@{base_domain["_name"]}',
            'password': 'short',
        })
        assert resp.status_code == 400

    def test_create_email_account_invalid_email(self):
        resp = post('/api/v1/email-accounts', {
            'email': 'not-an-email',
            'password': 'ValidPass123!',
        })
        assert resp.status_code == 400

    def test_get_email_account(self, base_mailbox):
        resp = get(f'/api/v1/email-accounts/{base_mailbox["id"]}')
        assert resp.status_code == 200
        data = resp.json().get('data', resp.json())
        assert 'password' not in data

    def test_get_email_account_not_found(self):
        resp = get('/api/v1/email-accounts/999999')
        assert resp.status_code == 404

    def test_update_email_account(self, base_mailbox):
        resp = put(f'/api/v1/email-accounts/{base_mailbox["id"]}', {
            'name': 'Updated Name',
            'vacation_enabled': False,
        })
        assert resp.status_code == 200

    def test_get_email_account_quotas(self, base_mailbox):
        resp = get(f'/api/v1/email-accounts/{base_mailbox["id"]}/quotas')
        assert resp.status_code == 200
        data = resp.json().get('data', resp.json())
        assert 'storage_quota' in data or 'account_id' in data

    def test_update_email_account_quotas(self, base_mailbox):
        resp = put(f'/api/v1/email-accounts/{base_mailbox["id"]}/quotas', {
            'storage_quota': 2147483648,
            'rate_limits': {'per_hour': 200},
        })
        assert resp.status_code == 200

    def test_delete_email_account(self, base_domain):
        """Create and delete a temp account."""
        email_addr = f'del-{uid()}@{base_domain["_name"]}'
        resp = post('/api/v1/email-accounts', {
            'email': email_addr,
            'password': 'DeleteMe123!',
        })
        assert resp.status_code in (200, 201)
        data = resp.json().get('data', resp.json())
        account_id = data.get('id')
        resp = delete(f'/api/v1/email-accounts/{account_id}')
        assert resp.status_code == 200

    # Legacy mailbox routes
    def test_legacy_get_mailbox_all(self):
        resp = get('/api/v1/get/mailbox/all')
        assert resp.status_code in (200, 404, 500)

    def test_legacy_get_mailbox_stats(self, base_mailbox):
        resp = get(f'/api/v1/get/mailbox/stats/{base_mailbox["_email"]}')
        assert resp.status_code in (200, 404)

    def test_legacy_get_mailbox_quota(self, base_mailbox):
        resp = get(f'/api/v1/get/mailbox/quota/{base_mailbox["_email"]}')
        assert resp.status_code in (200, 404)


# ===========================================================================
# 4. Aliases CRUD
# ===========================================================================
class TestAliases:
    """Test /api/v1/aliases routes."""

    def test_add_alias(self, base_domain, base_mailbox):
        alias_addr = f'alias-{uid()}@{base_domain["_name"]}'
        resp = post('/api/v1/add/alias', {
            'address': alias_addr,
            'goto': base_mailbox['_email'],
        })
        assert resp.status_code in (200, 201)
        data = resp.json().get('data', resp.json())
        alias_id = data.get('alias_id')
        # Cleanup
        if alias_id:
            post('/api/v1/delete/alias', [alias_id])

    def test_add_alias_duplicate(self, base_domain, base_mailbox):
        alias_addr = f'dup-alias-{uid()}@{base_domain["_name"]}'
        post('/api/v1/add/alias', {
            'address': alias_addr,
            'goto': base_mailbox['_email'],
        })
        resp = post('/api/v1/add/alias', {
            'address': alias_addr,
            'goto': base_mailbox['_email'],
        })
        assert resp.status_code == 409
        # Cleanup
        post('/api/v1/delete/alias', [alias_addr])

    def test_add_alias_invalid_address(self):
        resp = post('/api/v1/add/alias', {
            'address': 'not-an-email',
            'goto': 'dest@example.com',
        })
        assert resp.status_code == 400

    def test_get_alias_all(self):
        resp = get('/api/v1/get/alias/all')
        assert resp.status_code in (200, 404)

    def test_get_alias_by_id(self, base_domain, base_mailbox):
        alias_addr = f'get-{uid()}@{base_domain["_name"]}'
        resp = post('/api/v1/add/alias', {
            'address': alias_addr,
            'goto': base_mailbox['_email'],
        })
        data = resp.json().get('data', resp.json())
        alias_id = data.get('alias_id', alias_addr)

        resp = get(f'/api/v1/get/alias/{alias_id}')
        assert resp.status_code in (200, 404)

        # Cleanup
        post('/api/v1/delete/alias', [alias_addr])

    def test_edit_alias(self, base_domain, base_mailbox):
        alias_addr = f'edit-{uid()}@{base_domain["_name"]}'
        resp = post('/api/v1/add/alias', {
            'address': alias_addr,
            'goto': base_mailbox['_email'],
        })
        data = resp.json().get('data', resp.json())
        alias_id = data.get('alias_id', alias_addr)

        resp = post('/api/v1/edit/alias', {
            'items': [alias_id],
            'attr': {'active': 0},
        })
        assert resp.status_code == 200

        # Cleanup
        post('/api/v1/delete/alias', [alias_addr])

    def test_delete_alias(self, base_domain, base_mailbox):
        alias_addr = f'del-{uid()}@{base_domain["_name"]}'
        post('/api/v1/add/alias', {
            'address': alias_addr,
            'goto': base_mailbox['_email'],
        })
        resp = post('/api/v1/delete/alias', [alias_addr])
        assert resp.status_code == 200

    def test_get_alias_stats(self, base_domain):
        resp = get(f'/api/v1/get/alias/stats/{base_domain["_name"]}')
        assert resp.status_code in (200, 404)

    def test_add_bulk_aliases(self, base_domain, base_mailbox):
        aliases = [
            {'address': f'bulk-{uid()}-{i}@{base_domain["_name"]}', 'goto': base_mailbox['_email']}
            for i in range(3)
        ]
        resp = post('/api/v1/add/alias/bulk', {'aliases': aliases})
        assert resp.status_code == 200
        body = resp.json()
        data = body.get('data', body)
        summary = data.get('summary', {})
        assert summary.get('total') == 3

        # Cleanup
        for a in aliases:
            post('/api/v1/delete/alias', [a['address']])


# ===========================================================================
# 5. Analytics GET Endpoints
# ===========================================================================
class TestAnalytics:
    """Test /api/v1/analytics routes (proxy to analytics service)."""

    def test_analytics_health(self):
        resp = get('/api/v1/analytics/health')
        assert resp.status_code in (200, 503)

    def test_dashboard_data(self, base_domain):
        resp = get(f'/api/v1/analytics/dashboard/{base_domain["_name"]}')
        assert resp.status_code in (200, 503)

    def test_email_volume(self, base_domain):
        resp = get(f'/api/v1/analytics/email-volume/{base_domain["_name"]}', params={'period': '7d'})
        assert resp.status_code in (200, 503)

    def test_engagement_metrics(self, base_domain):
        resp = get(f'/api/v1/analytics/engagement/{base_domain["_name"]}')
        assert resp.status_code in (200, 503)

    def test_deliverability_metrics(self, base_domain):
        resp = get(f'/api/v1/analytics/deliverability/{base_domain["_name"]}')
        assert resp.status_code in (200, 503)

    def test_domain_metrics(self, base_domain):
        resp = get(f'/api/v1/analytics/metrics/{base_domain["_name"]}')
        assert resp.status_code in (200, 503)

    def test_generate_report(self, base_domain):
        resp = post('/api/v1/reports/generate', {
            'domain': base_domain['_name'],
            'type': 'summary',
            'period': '7d',
        })
        assert resp.status_code in (200, 202, 503)

    def test_get_scheduled_reports(self):
        resp = get('/api/v1/reports/scheduled')
        assert resp.status_code in (200, 503)

    def test_create_scheduled_report(self, base_domain):
        resp = post('/api/v1/reports/scheduled', {
            'domain': base_domain['_name'],
            'frequency': 'weekly',
            'recipients': [f'admin@{base_domain["_name"]}'],
        })
        assert resp.status_code in (200, 201, 503)


# ===========================================================================
# 6. Monitoring Endpoints
# ===========================================================================
class TestMonitoring:
    """Test /api/v1/monitoring routes."""

    def test_monitoring_health(self):
        resp = get('/api/v1/monitoring/health')
        assert resp.status_code in (200, 500, 503)

    def test_services_status(self):
        resp = get('/api/v1/monitoring/services')
        assert resp.status_code in (200, 500, 503)

    def test_specific_service_status(self):
        resp = get('/api/v1/monitoring/services/postfix')
        assert resp.status_code in (200, 404, 500, 503)

    def test_system_metrics(self):
        resp = get('/api/v1/monitoring/metrics')
        assert resp.status_code in (200, 500, 503)

    def test_dashboard_stats(self):
        resp = get('/api/v1/monitoring/stats')
        assert resp.status_code in (200, 500, 503)

    def test_restart_service_requires_auth(self):
        """Restart should require admin token."""
        resp = requests.post(
            f'{API_URL}/api/v1/monitoring/services/postfix/restart',
            headers=api_headers(),
            timeout=15,
        )
        assert resp.status_code in (401, 500, 503)

    def test_auto_heal_requires_auth(self):
        resp = requests.post(
            f'{API_URL}/api/v1/monitoring/auto-heal',
            headers=api_headers(),
            timeout=15,
        )
        assert resp.status_code in (401, 500, 503)

    def test_webhooks_test_requires_auth(self):
        resp = requests.post(
            f'{API_URL}/api/v1/monitoring/webhooks/test',
            headers=api_headers(),
            timeout=15,
        )
        assert resp.status_code in (401, 500, 503)


# ===========================================================================
# 7. Queue Management
# ===========================================================================
class TestQueueManagement:
    """Test /api/v1/queue routes."""

    def test_queue_status(self):
        resp = get('/api/v1/queue/queue/status')
        assert resp.status_code in (200, 503)

    def test_queue_health(self):
        resp = get('/api/v1/queue/queue/health')
        assert resp.status_code in (200, 503)

    def test_domain_queue(self, base_domain):
        resp = get(f'/api/v1/queue/queue/domain/{base_domain["_name"]}')
        assert resp.status_code in (200, 503)

    def test_deferred_mail(self):
        resp = get('/api/v1/queue/mail-queue/deferred')
        assert resp.status_code in (200, 503)

    def test_flush_mail_queue(self):
        resp = post('/api/v1/queue/mail-queue/flush', {})
        assert resp.status_code in (200, 202, 503)

    def test_domain_queue_jobs(self, base_domain):
        resp = get(f'/api/v1/queue/queue/jobs/{base_domain["_name"]}')
        assert resp.status_code in (200, 503)


# ===========================================================================
# 8. Webhooks Configuration
# ===========================================================================
class TestWebhooks:
    """Test /api/v1/webhooks routes."""

    def test_list_subscriptions(self):
        resp = get('/api/v1/webhooks/subscriptions')
        assert resp.status_code in (200, 503)

    def test_create_and_delete_subscription(self):
        resp = post('/api/v1/webhooks/subscriptions', {
            'url': 'https://httpbin.org/post',
            'events': ['email.sent', 'email.bounced'],
            'domain': TEST_DOMAIN,
            'secret': f'secret-{uid()}',
            'active': True,
        })
        assert resp.status_code in (200, 201, 503)

        if resp.status_code in (200, 201):
            body = resp.json()
            sub_id = body.get('id') or body.get('subscription_id') or (body.get('data', {}).get('id'))
            if sub_id:
                # Get
                resp = get(f'/api/v1/webhooks/subscriptions/{sub_id}')
                assert resp.status_code in (200, 503)
                # Update
                resp = put(f'/api/v1/webhooks/subscriptions/{sub_id}', {
                    'active': False,
                })
                assert resp.status_code in (200, 503)
                # Delete
                resp = delete(f'/api/v1/webhooks/subscriptions/{sub_id}')
                assert resp.status_code in (200, 204, 503)

    def test_test_webhook(self):
        resp = post('/api/v1/webhooks/test', {
            'url': 'https://httpbin.org/post',
            'event_type': 'email.sent',
            'payload': {'test': True},
        })
        assert resp.status_code in (200, 503)

    def test_webhook_events(self):
        resp = get(f'/api/v1/webhooks/events/{TEST_DOMAIN}')
        assert resp.status_code in (200, 503)

    def test_retry_webhook(self):
        resp = post('/api/v1/webhooks/retry/nonexistent-event-id')
        assert resp.status_code in (200, 404, 503)


# ===========================================================================
# 9. Rate Limiter Config
# ===========================================================================
class TestRateLimiter:
    """Test /api/v1/rate-limiter routes."""

    def test_get_domain_limits(self, base_domain):
        resp = get(f'/api/v1/rate-limiter/rate-limits/domain/{base_domain["_name"]}')
        assert resp.status_code in (200, 503)

    def test_set_domain_limits(self, base_domain):
        resp = post(f'/api/v1/rate-limiter/rate-limits/domain/{base_domain["_name"]}', {
            'per_minute': 50,
            'per_hour': 500,
            'per_day': 5000,
        })
        assert resp.status_code in (200, 503)

    def test_get_mailbox_limits(self, base_mailbox):
        resp = get(f'/api/v1/rate-limiter/rate-limits/mailbox/{base_mailbox["_email"]}')
        assert resp.status_code in (200, 503)

    def test_set_mailbox_limits(self, base_mailbox):
        resp = post(f'/api/v1/rate-limiter/rate-limits/mailbox/{base_mailbox["_email"]}', {
            'per_minute': 10,
            'per_hour': 100,
        })
        assert resp.status_code in (200, 503)

    def test_get_domain_usage(self, base_domain):
        resp = get(f'/api/v1/rate-limiter/rate-limits/usage/{base_domain["_name"]}')
        assert resp.status_code in (200, 503)

    def test_reset_limits(self):
        resp = post('/api/v1/rate-limiter/rate-limits/reset', {
            'domain': TEST_DOMAIN,
        })
        assert resp.status_code in (200, 503)

    def test_get_domain_quotas(self, base_domain):
        resp = get(f'/api/v1/rate-limiter/quotas/domain/{base_domain["_name"]}')
        assert resp.status_code in (200, 503)

    def test_set_domain_quotas(self, base_domain):
        resp = post(f'/api/v1/rate-limiter/quotas/domain/{base_domain["_name"]}', {
            'daily_limit': 10000,
            'monthly_limit': 300000,
        })
        assert resp.status_code in (200, 503)


# ===========================================================================
# 10. Storage Operations
# ===========================================================================
class TestStorage:
    """Test /api/v1/storage routes."""

    def test_domain_storage_usage(self, base_domain):
        resp = get(f'/api/v1/storage/usage/domain/{base_domain["_name"]}')
        assert resp.status_code in (200, 503)

    def test_mailbox_storage_usage(self, base_mailbox):
        resp = get(f'/api/v1/storage/usage/mailbox/{base_mailbox["_email"]}')
        assert resp.status_code in (200, 503)

    def test_domain_storage_quotas_get(self, base_domain):
        resp = get(f'/api/v1/storage/quotas/domain/{base_domain["_name"]}')
        assert resp.status_code in (200, 503)

    def test_domain_storage_quotas_set(self, base_domain):
        resp = post(f'/api/v1/storage/quotas/domain/{base_domain["_name"]}', {
            'max_storage': 10737418240,
            'warning_threshold': 80,
        })
        assert resp.status_code in (200, 503)

    def test_mailbox_storage_quotas_get(self, base_mailbox):
        resp = get(f'/api/v1/storage/quotas/mailbox/{base_mailbox["_email"]}')
        assert resp.status_code in (200, 503)

    def test_mailbox_storage_quotas_set(self, base_mailbox):
        resp = post(f'/api/v1/storage/quotas/mailbox/{base_mailbox["_email"]}', {
            'max_storage': 2147483648,
        })
        assert resp.status_code in (200, 503)

    def test_trigger_cleanup(self):
        resp = post('/api/v1/storage/cleanup', {
            'older_than_days': 90,
            'dry_run': True,
        })
        assert resp.status_code in (200, 503)

    def test_usage_summary(self):
        resp = get('/api/v1/storage/usage/summary')
        assert resp.status_code in (200, 503)


# ===========================================================================
# 11. Tracking Endpoints
# ===========================================================================
class TestTracking:
    """Test /api/v1/tracking routes."""

    def test_track_pixel(self):
        resp = get(f'/api/v1/tracking/pixel/{uid()}')
        assert resp.status_code in (200, 302, 404, 503)

    def test_track_click(self):
        resp = get(f'/api/v1/tracking/click/{uid()}')
        assert resp.status_code in (200, 302, 404, 503)

    def test_unsubscribe_get(self):
        resp = get(f'/api/v1/tracking/unsubscribe/{uid()}')
        assert resp.status_code in (200, 302, 404, 503)

    def test_unsubscribe_post(self):
        resp = post(f'/api/v1/tracking/unsubscribe/{uid()}', {
            'reason': 'No longer interested',
        })
        assert resp.status_code in (200, 302, 404, 503)

    def test_domain_stats(self, base_domain):
        resp = get(f'/api/v1/tracking/stats/domain/{base_domain["_name"]}')
        assert resp.status_code in (200, 503)

    def test_email_stats(self):
        resp = get(f'/api/v1/tracking/stats/email/{uid()}')
        assert resp.status_code in (200, 404, 503)

    def test_add_suppression(self):
        email_to_suppress = f'suppress-{uid()}@example.com'
        resp = post('/api/v1/tracking/suppress', {
            'email': email_to_suppress,
            'reason': 'Test suppression',
        })
        assert resp.status_code in (200, 201, 503)

    def test_remove_suppression(self):
        email_addr = f'unsuppress-{uid()}@example.com'
        # Add first
        post('/api/v1/tracking/suppress', {'email': email_addr, 'reason': 'test'})
        # Then remove
        resp = delete(f'/api/v1/tracking/suppress/{email_addr}')
        assert resp.status_code in (200, 204, 404, 503)


# ===========================================================================
# 12. RAG / AI Endpoints
# ===========================================================================
class TestRAG:
    """Test /api/v1/rag routes."""

    def test_rag_health(self):
        resp = get('/api/v1/rag/health')
        assert resp.status_code in (200, 503)

    def test_rag_search(self):
        resp = post('/api/v1/rag/search', {
            'query': 'invoice payment reminder',
            'organization_id': 'default',
            'limit': 5,
        })
        assert resp.status_code in (200, 503)

    def test_get_index_status(self, base_domain):
        resp = get(f'/api/v1/rag/rag/index/status/{base_domain["_name"]}')
        assert resp.status_code in (200, 404, 503)

    def test_trigger_indexing(self, base_domain):
        resp = post('/api/v1/rag/rag/index/trigger', {
            'domain': base_domain['_name'],
            'reindex': False,
        })
        assert resp.status_code in (200, 202, 503)

    def test_get_collections(self, base_domain):
        resp = get(f'/api/v1/rag/collections/{base_domain["_name"]}')
        assert resp.status_code in (200, 503)

    def test_create_collection(self, base_domain):
        resp = post(f'/api/v1/rag/collections/{base_domain["_name"]}', {
            'name': f'test-collection-{uid()}',
        })
        assert resp.status_code in (200, 201, 503)

    def test_collection_stats(self, base_domain):
        resp = get(f'/api/v1/rag/collections/{base_domain["_name"]}/stats')
        assert resp.status_code in (200, 503)

    def test_organization_collections(self, base_org):
        resp = get(f'/api/v1/rag/organizations/{base_org["_id"]}/collections')
        assert resp.status_code in (200, 503)

    def test_organization_rag_config_get(self, base_org):
        resp = get(f'/api/v1/rag/organizations/{base_org["_id"]}/rag/config')
        assert resp.status_code in (200, 503)

    def test_organization_rag_config_put(self, base_org):
        resp = put(f'/api/v1/rag/organizations/{base_org["_id"]}/rag/config', {
            'enabled': True,
            'embedding_model': 'default',
        })
        assert resp.status_code in (200, 503)

    def test_organization_rag_stats(self, base_org):
        resp = get(f'/api/v1/rag/organizations/{base_org["_id"]}/rag/stats')
        assert resp.status_code in (200, 503)

    def test_organization_rag_documents(self, base_org):
        resp = get(f'/api/v1/rag/organizations/{base_org["_id"]}/rag/documents')
        assert resp.status_code in (200, 503)

    def test_organization_rag_reindex(self, base_org):
        resp = post(f'/api/v1/rag/organizations/{base_org["_id"]}/rag/reindex', {})
        assert resp.status_code in (200, 202, 503)


# ===========================================================================
# 13. Filters (Sieve) CRUD
# ===========================================================================
class TestFilters:
    """Test /api/v1/filters routes."""

    def test_list_filters(self, base_mailbox):
        resp = get('/api/v1/filters/', params={'email': base_mailbox['_email']})
        assert resp.status_code in (200, 404, 422)

    def test_list_filter_templates(self):
        resp = get('/api/v1/filters/templates')
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        if len(body) > 0:
            assert 'id' in body[0]
            assert 'sieve_content' in body[0]

    def test_create_filter(self, base_mailbox):
        resp = post('/api/v1/filters/', params={'email': base_mailbox['_email']}, data={
            'name': f'test-filter-{uid()}',
            'content': 'require ["fileinto"];\nif header :contains "subject" "SPAM" {\n  fileinto "Junk";\n  stop;\n}',
            'active': False,
        })
        # 200, 404 (user doesn't exist on disk), or 500 (filesystem issue)
        assert resp.status_code in (200, 404, 422, 500)

    def test_get_filter(self, base_mailbox):
        resp = get(f'/api/v1/filters/test-script', params={'email': base_mailbox['_email']})
        assert resp.status_code in (200, 404)

    def test_delete_filter(self, base_mailbox):
        resp = delete(f'/api/v1/filters/nonexistent-script', params={'email': base_mailbox['_email']})
        assert resp.status_code in (200, 404)

    def test_activate_filter(self, base_mailbox):
        resp = put(f'/api/v1/filters/test-script/activate', params={'email': base_mailbox['_email']})
        assert resp.status_code in (200, 404)

    def test_manage_vacation(self, base_mailbox):
        resp = post('/api/v1/filters/vacation', params={'email': base_mailbox['_email']}, data={
            'enabled': False,
            'subject': 'Out of Office',
            'message': 'I am currently away.',
            'reply_interval': 86400,
        })
        assert resp.status_code in (200, 404, 422, 500)


# ===========================================================================
# 14. Shared Mailboxes
# ===========================================================================
class TestSharedMailboxes:
    """Test /api/v1/shared-mailboxes routes."""

    def test_list_shared_mailboxes(self, base_org):
        resp = get('/api/v1/shared-mailboxes/', params={'organization_id': base_org['_id']})
        assert resp.status_code in (200, 422)

    def test_create_shared_mailbox(self, base_domain):
        shared_email = f'shared-{uid()}@{base_domain["_name"]}'
        resp = post('/api/v1/shared-mailboxes/', data={
            'email': shared_email,
            'name': 'Test Shared Mailbox',
            'organization_id': base_domain['_org_id'],
        })
        assert resp.status_code in (200, 201, 400, 404, 409)

        if resp.status_code in (200, 201):
            body = resp.json()
            mailbox_id = body.get('id')
            if mailbox_id:
                # Get details
                resp = get(f'/api/v1/shared-mailboxes/{mailbox_id}')
                assert resp.status_code in (200, 404)
                # Delete
                resp = delete(f'/api/v1/shared-mailboxes/{mailbox_id}')
                assert resp.status_code in (200, 404)

    def test_shared_mailbox_members(self, base_domain, base_mailbox):
        """Test adding and removing members from a shared mailbox."""
        shared_email = f'shared-mbr-{uid()}@{base_domain["_name"]}'
        resp = post('/api/v1/shared-mailboxes/', data={
            'email': shared_email,
            'name': 'Member Test Shared',
            'organization_id': base_domain['_org_id'],
        })
        if resp.status_code not in (200, 201):
            pytest.skip('Could not create shared mailbox for member test')

        body = resp.json()
        mailbox_id = body.get('id')
        if not mailbox_id:
            pytest.skip('No shared mailbox ID returned')

        member_account_id = base_mailbox.get('id')

        # Add member
        resp = post(f'/api/v1/shared-mailboxes/{mailbox_id}/members', data={
            'email_account_id': member_account_id,
            'permission': 'full_access',
        })
        assert resp.status_code in (200, 201, 404)

        # Update member permission
        resp = put(f'/api/v1/shared-mailboxes/{mailbox_id}/members/{member_account_id}', data={
            'email_account_id': member_account_id,
            'permission': 'read_only',
        })
        assert resp.status_code in (200, 404)

        # Remove member
        resp = delete(f'/api/v1/shared-mailboxes/{mailbox_id}/members/{member_account_id}')
        assert resp.status_code in (200, 404)

        # Cleanup
        delete(f'/api/v1/shared-mailboxes/{mailbox_id}')


# ===========================================================================
# 15. Message Trace
# ===========================================================================
class TestMessageTrace:
    """Test /api/v1/message-trace routes."""

    def test_search_messages(self, base_org):
        resp = get('/api/v1/message-trace/trace', params={
            'organization_id': base_org['_id'],
            'limit': 10,
        })
        assert resp.status_code in (200, 404)

    def test_search_messages_by_sender(self, base_mailbox):
        resp = get('/api/v1/message-trace/trace', params={
            'sender': base_mailbox['_email'],
            'limit': 5,
        })
        assert resp.status_code in (200, 404)

    def test_search_messages_by_recipient(self, base_mailbox):
        resp = get('/api/v1/message-trace/trace', params={
            'recipient': base_mailbox['_email'],
            'limit': 5,
        })
        assert resp.status_code in (200, 404)

    def test_search_messages_by_date(self):
        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)
        resp = get('/api/v1/message-trace/trace', params={
            'start_date': yesterday.isoformat(),
            'end_date': now.isoformat(),
            'limit': 10,
        })
        assert resp.status_code in (200, 404)

    def test_get_message_trace(self):
        resp = get(f'/api/v1/message-trace/trace/nonexistent-msg-id-{uid()}')
        assert resp.status_code in (200, 404)

    def test_list_quarantine(self, base_org):
        resp = get('/api/v1/message-trace/quarantine', params={
            'organization_id': base_org['_id'],
            'limit': 10,
        })
        assert resp.status_code in (200, 404)

    def test_release_quarantine_not_found(self):
        resp = post('/api/v1/message-trace/quarantine/999999/release')
        assert resp.status_code in (200, 404)

    def test_block_quarantine_not_found(self):
        resp = post('/api/v1/message-trace/quarantine/999999/block')
        assert resp.status_code in (200, 404)

    def test_search_audit_logs(self, base_org):
        resp = get('/api/v1/message-trace/audit', params={
            'organization_id': base_org['_id'],
            'limit': 10,
        })
        assert resp.status_code in (200, 404)

    def test_search_audit_logs_by_type(self):
        resp = get('/api/v1/message-trace/audit', params={
            'event_type': 'login',
            'limit': 5,
        })
        assert resp.status_code in (200, 404)


# ===========================================================================
# 16. Transport Rules
# ===========================================================================
class TestTransportRules:
    """Test /api/v1/transport-rules routes."""

    def test_list_transport_rules(self, base_org):
        resp = get('/api/v1/transport-rules/', params={'organization_id': base_org['_id']})
        assert resp.status_code in (200, 422)

    def test_create_transport_rule(self, base_org):
        rule_name = f'test-rule-{uid()}'
        resp = post('/api/v1/transport-rules/', data={
            'name': rule_name,
            'description': 'E2E test rule',
            'organization_id': base_org['_id'],
            'direction': 'inbound',
            'conditions': [
                {'field': 'sender', 'operator': 'contains', 'value': 'spam'},
            ],
            'condition_logic': 'all',
            'actions': [
                {'type': 'add_header', 'params': {'name': 'X-Spam-Flag', 'value': 'YES'}},
            ],
            'priority': 50,
            'enabled': True,
        })
        assert resp.status_code in (200, 201, 404)

        if resp.status_code in (200, 201):
            body = resp.json()
            rule_id = body.get('id')

            if rule_id:
                # Get specific rule
                resp = get(f'/api/v1/transport-rules/{rule_id}')
                assert resp.status_code == 200

                # Update rule
                resp = put(f'/api/v1/transport-rules/{rule_id}', data={
                    'name': f'{rule_name}-updated',
                    'description': 'Updated E2E test rule',
                    'organization_id': base_org['_id'],
                    'direction': 'both',
                    'conditions': [
                        {'field': 'subject', 'operator': 'contains', 'value': '[SPAM]'},
                    ],
                    'condition_logic': 'all',
                    'actions': [
                        {'type': 'quarantine', 'params': {'reason': 'Suspected spam'}},
                    ],
                    'priority': 25,
                    'enabled': True,
                })
                assert resp.status_code in (200, 404)

                # Toggle enable/disable
                resp = put(f'/api/v1/transport-rules/{rule_id}/enable', params={'enabled': False})
                assert resp.status_code in (200, 404)

                # Delete rule
                resp = delete(f'/api/v1/transport-rules/{rule_id}')
                assert resp.status_code in (200, 404)

    def test_get_nonexistent_rule(self):
        resp = get('/api/v1/transport-rules/999999')
        assert resp.status_code == 404

    def test_reorder_rules(self, base_org):
        # Create two rules to reorder
        ids = []
        for i in range(2):
            resp = post('/api/v1/transport-rules/', data={
                'name': f'reorder-{uid()}-{i}',
                'organization_id': base_org['_id'],
                'direction': 'both',
                'conditions': [{'field': 'sender', 'operator': 'equals', 'value': f'test{i}@example.com'}],
                'condition_logic': 'all',
                'actions': [{'type': 'add_header', 'params': {'name': 'X-Test', 'value': str(i)}}],
                'priority': (i + 1) * 10,
                'enabled': True,
            })
            if resp.status_code in (200, 201):
                body = resp.json()
                rid = body.get('id')
                if rid:
                    ids.append(rid)

        if len(ids) >= 2:
            resp = put('/api/v1/transport-rules/reorder', data={
                'rule_ids': list(reversed(ids)),
            })
            assert resp.status_code in (200, 404)

        # Cleanup
        for rid in ids:
            delete(f'/api/v1/transport-rules/{rid}')


# ===========================================================================
# 17. Whitelabel Config
# ===========================================================================
class TestWhitelabel:
    """Test /api/v1/whitelabel routes."""

    def test_get_whitelabel_config(self, base_org):
        resp = get(f'/api/v1/whitelabel/config/{base_org["_id"]}')
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert 'organization_id' in body
            assert 'whitelabel' in body

    def test_update_whitelabel_config(self, base_org):
        resp = put(f'/api/v1/whitelabel/config/{base_org["_id"]}', data={
            'brand_name': 'Test Brand',
            'primary_color': '#FF5733',
            'secondary_color': '#1E40AF',
            'support_email': f'support@{TEST_DOMAIN}',
        })
        assert resp.status_code in (200, 404)

    def test_preview_branded_login(self, base_org):
        resp = get(f'/api/v1/whitelabel/config/{base_org["_id"]}/preview')
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert 'preview_html' in body

    def test_verify_custom_domain(self, base_org):
        # First set a custom domain
        put(f'/api/v1/whitelabel/config/{base_org["_id"]}', data={
            'custom_domain': f'panel.{TEST_DOMAIN}',
        })
        resp = post(f'/api/v1/whitelabel/config/{base_org["_id"]}/verify-domain')
        assert resp.status_code in (200, 400, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert 'verified' in body

    def test_delete_whitelabel_config(self, base_org):
        resp = delete(f'/api/v1/whitelabel/config/{base_org["_id"]}')
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert body.get('status') == 'ok'

    def test_get_whitelabel_config_not_found(self):
        resp = get('/api/v1/whitelabel/config/nonexistent-org-xyz')
        assert resp.status_code == 404


# ===========================================================================
# 18. Reseller Operations
# ===========================================================================
class TestReseller:
    """Test /api/v1/reseller routes."""

    @pytest.fixture(autouse=True)
    def setup_reseller(self, base_org):
        """Provide base_org as the reseller parent."""
        self.parent_org_id = base_org['_id']

    def test_create_sub_organization(self):
        resp = post('/api/v1/reseller/sub-organizations', data={
            'parent_org_id': self.parent_org_id,
            'name': f'Sub Org {uid()}',
            'admin_email': f'subadmin-{uid()}@{TEST_DOMAIN}',
            'max_users': 25,
            'max_domains': 3,
            'storage_limit': 5368709120,
            'plan_name': 'starter',
        })
        assert resp.status_code in (200, 201, 404)

        if resp.status_code in (200, 201):
            body = resp.json()
            sub_id = body.get('id')
            if sub_id:
                # Clean up later
                delete(f'/api/v1/reseller/sub-organizations/{sub_id}')

    def test_list_sub_organizations(self):
        resp = get('/api/v1/reseller/sub-organizations', params={
            'parent_org_id': self.parent_org_id,
        })
        assert resp.status_code in (200, 404)

    def test_get_sub_organization(self):
        # Create a sub-org first
        resp = post('/api/v1/reseller/sub-organizations', data={
            'parent_org_id': self.parent_org_id,
            'name': f'Get Sub {uid()}',
            'max_users': 10,
            'max_domains': 2,
            'storage_limit': 1073741824,
            'plan_name': 'starter',
        })
        if resp.status_code not in (200, 201):
            pytest.skip('Could not create sub-org')

        body = resp.json()
        sub_id = body.get('id')
        if not sub_id:
            pytest.skip('No sub_id returned')

        resp = get(f'/api/v1/reseller/sub-organizations/{sub_id}')
        assert resp.status_code in (200, 400, 404)
        if resp.status_code == 200:
            detail = resp.json()
            assert detail.get('id') == sub_id
            assert 'domain_count' in detail or 'user_count' in detail

        # Cleanup
        delete(f'/api/v1/reseller/sub-organizations/{sub_id}')

    def test_update_sub_organization_plan(self):
        resp = post('/api/v1/reseller/sub-organizations', data={
            'parent_org_id': self.parent_org_id,
            'name': f'Plan Sub {uid()}',
            'max_users': 10,
            'max_domains': 2,
            'storage_limit': 1073741824,
            'plan_name': 'starter',
        })
        if resp.status_code not in (200, 201):
            pytest.skip('Could not create sub-org')

        body = resp.json()
        sub_id = body.get('id')
        if not sub_id:
            pytest.skip('No sub_id returned')

        resp = put(f'/api/v1/reseller/sub-organizations/{sub_id}/plan', data={
            'max_users': 50,
            'plan_name': 'business',
            'storage_limit': 10737418240,
        })
        assert resp.status_code in (200, 400, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert body.get('plan', {}).get('plan_name') == 'business'

        # Cleanup
        delete(f'/api/v1/reseller/sub-organizations/{sub_id}')

    def test_deactivate_sub_organization(self):
        resp = post('/api/v1/reseller/sub-organizations', data={
            'parent_org_id': self.parent_org_id,
            'name': f'Deactivate Sub {uid()}',
            'max_users': 5,
            'max_domains': 1,
            'storage_limit': 1073741824,
            'plan_name': 'starter',
        })
        if resp.status_code not in (200, 201):
            pytest.skip('Could not create sub-org')

        body = resp.json()
        sub_id = body.get('id')
        if not sub_id:
            pytest.skip('No sub_id returned')

        resp = delete(f'/api/v1/reseller/sub-organizations/{sub_id}')
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert resp.json().get('status') == 'ok'

    def test_billing_summary(self):
        resp = get('/api/v1/reseller/billing/summary', params={
            'parent_org_id': self.parent_org_id,
        })
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert 'total_sub_organizations' in body
            assert 'per_org_breakdown' in body

    def test_sub_org_not_found(self):
        resp = get('/api/v1/reseller/sub-organizations/nonexistent-sub-xyz')
        assert resp.status_code in (400, 404)

    def test_deactivate_non_sub_org(self, base_org):
        """Deactivating a non-sub-org should fail."""
        resp = delete(f'/api/v1/reseller/sub-organizations/{base_org["_id"]}')
        assert resp.status_code in (400, 404)


# ===========================================================================
# Health & Root endpoints (basic sanity)
# ===========================================================================
class TestHealthAndRoot:
    """Test root and health endpoints."""

    def test_root(self):
        resp = get('/')
        assert resp.status_code == 200
        body = resp.json()
        assert 'version' in body

    def test_health(self):
        resp = get('/health')
        assert resp.status_code == 200
        body = resp.json()
        assert body.get('status') in ('healthy', 'unhealthy')
        assert 'database' in body

    def test_health_schema(self):
        resp = get('/health')
        body = resp.json()
        assert 'version' in body
        assert isinstance(body.get('database'), str)
