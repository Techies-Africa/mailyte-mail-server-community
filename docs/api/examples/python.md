# Python Examples

Python examples using the `requests` library.

## Setup

Install the requests library if you do not have it:

```bash
pip install requests
```

Create a reusable client:

```python
import requests


class MailyteClient:
    def __init__(self, base_url, api_key):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key, "Content-Type": "application/json"})

    def _url(self, path):
        return f"{self.base_url}/api/v1{path}"

    def _check(self, resp):
        resp.raise_for_status()
        body = resp.json()
        if body.get("type") == "error":
            raise Exception(f"API error: {body['msg']}")
        return body

    # Organizations
    def list_organizations(self, page=1, per_page=50):
        resp = self.session.get(
            self._url("/organizations"), params={"page": page, "per_page": per_page}
        )
        return self._check(resp)

    def get_organization(self, org_id):
        resp = self.session.get(self._url(f"/organizations/{org_id}"))
        return self._check(resp)

    def create_organization(self, data):
        resp = self.session.post(self._url("/organizations"), json=data)
        return self._check(resp)

    def update_organization(self, org_id, data):
        resp = self.session.put(self._url(f"/organizations/{org_id}"), json=data)
        return self._check(resp)

    def delete_organization(self, org_id):
        resp = self.session.delete(self._url(f"/organizations/{org_id}"))
        return self._check(resp)

    # Domains
    def list_domains(self, organization_id=None, page=1, per_page=50):
        params = {"page": page, "per_page": per_page}
        if organization_id:
            params["organization_id"] = organization_id
        resp = self.session.get(self._url("/domains"), params=params)
        return self._check(resp)

    def create_domain(self, data):
        resp = self.session.post(self._url("/domains"), json=data)
        return self._check(resp)

    def delete_domain(self, domain_id):
        resp = self.session.delete(self._url(f"/domains/{domain_id}"))
        return self._check(resp)

    # Email accounts
    def list_email_accounts(self, domain_id=None, organization_id=None, page=1):
        params = {"page": page}
        if domain_id:
            params["domain_id"] = domain_id
        if organization_id:
            params["organization_id"] = organization_id
        resp = self.session.get(self._url("/email-accounts"), params=params)
        return self._check(resp)

    def create_email_account(self, data):
        resp = self.session.post(self._url("/email-accounts"), json=data)
        return self._check(resp)

    def update_email_account(self, account_id, data):
        resp = self.session.put(self._url(f"/email-accounts/{account_id}"), json=data)
        return self._check(resp)

    def delete_email_account(self, account_id):
        resp = self.session.delete(self._url(f"/email-accounts/{account_id}"))
        return self._check(resp)

    # Webhooks
    def create_webhook(self, data):
        resp = self.session.post(self._url("/webhooks/endpoints"), json=data)
        return self._check(resp)

    def test_webhook(self, endpoint_id):
        resp = self.session.post(self._url(f"/webhooks/endpoints/{endpoint_id}/test"))
        return self._check(resp)

    # RAG search
    def search_emails(self, query, organization_id=None, limit=10):
        data = {"query": query, "limit": limit}
        if organization_id:
            data["organization_id"] = organization_id
        resp = self.session.post(self._url("/rag/search"), json=data)
        return self._check(resp)

    # Health
    def health(self):
        resp = self.session.get(f"{self.base_url}/health")
        return resp.json()
```

## Usage Examples

### Initialize the Client

```python
client = MailyteClient(base_url="http://your-server:5000", api_key="mk_live_your_api_key_here")
```

### Create an Organization

```python
org = client.create_organization(
    {
        "id": "acme",
        "name": "Acme Corp",
        "admin_email": "admin@acme.com",
        "rate_limits": {"inbound_hourly": 5000, "outbound_hourly": 2000},
        "storage_quotas": {"storage_quota_mb": 51200},
    }
)
print(f"Created org: {org['data']['id']}")
```

### Add a Domain

```python
domain = client.create_domain(
    {
        "domain": "acme.com",
        "organization_id": "acme",
        "max_quota": 10737418240,
        "max_users": 500,
        "dkim_enabled": True,
    }
)
print(f"Created domain: {domain['data']['domain']} (ID: {domain['data']['id']})")
```

### Create Email Accounts in Bulk

```python
users = [
    {"email": "john@acme.com", "password": "SecurePass123", "name": "John Doe"},
    {"email": "jane@acme.com", "password": "SecurePass456", "name": "Jane Smith"},
    {"email": "admin@acme.com", "password": "AdminPass789", "name": "Admin"},
]

for user in users:
    try:
        account = client.create_email_account(user)
        print(f"Created: {account['data']['email']}")
    except Exception as e:
        print(f"Failed to create {user['email']}: {e}")
```

### List Accounts with Pagination

```python
page = 1
while True:
    result = client.list_email_accounts(organization_id="acme", page=page)
    items = result["data"]["items"]
    pagination = result["data"]["pagination"]

    for account in items:
        print(f"  {account['email']} - {account['status']}")

    if page >= pagination["total_pages"]:
        break
    page += 1
```

### Set Up a Webhook

```python
webhook = client.create_webhook(
    {
        "url": "https://hooks.acme.com/mailyte",
        "description": "Production events",
        "event_types": ["email.sent", "email.delivered", "email.bounced"],
    }
)

# Save this secret securely
secret = webhook["data"]["secret"]
print(f"Webhook created. Secret: {secret}")

# Test it
client.test_webhook(webhook["data"]["id"])
print("Test event sent.")
```

### Search Emails with RAG

```python
results = client.search_emails(
    query="contract renewal documents from January", organization_id="acme", limit=5
)

for result in results.get("results", []):
    print(f"[{result['score']:.2f}] {result['subject']} - {result['from']}")
```

### Check Health

```python
health = client.health()
print(f"Status: {health['status']}, DB: {health['database']}")
```

### Error Handling

```python
import requests

try:
    result = client.create_organization({"id": "acme", "name": "Acme Corp"})
except requests.exceptions.HTTPError as e:
    body = e.response.json()
    if e.response.status_code == 409:
        print(f"Already exists: {body['msg']}")
    elif e.response.status_code == 400:
        errors = body.get("data", {}).get("errors", [])
        for err in errors:
            print(f"Validation error: {err}")
    else:
        print(f"HTTP {e.response.status_code}: {body['msg']}")
except Exception as e:
    print(f"Error: {e}")
```

### Full Setup Script

```python
"""Set up a complete organization with domain, accounts, and aliases."""

from mailyte_client import MailyteClient  # Save the class above as mailyte_client.py

client = MailyteClient("http://your-server:5000", "mk_live_your_api_key_here")

# 1. Create organization
client.create_organization({"id": "acme", "name": "Acme Corp", "admin_email": "admin@acme.com"})
print("Organization created.")

# 2. Add domain
domain = client.create_domain({"domain": "acme.com", "organization_id": "acme"})
print(f"Domain created (ID: {domain['data']['id']}).")

# 3. Create accounts
for name in ["john", "jane", "admin"]:
    client.create_email_account(
        {"email": f"{name}@acme.com", "password": "SecurePass123", "name": name.capitalize()}
    )
    print(f"Account {name}@acme.com created.")

# 4. Create aliases
import requests

resp = requests.post(
    "http://your-server:5000/api/v1/add/alias/bulk",
    headers={"X-API-Key": "mk_live_your_api_key_here", "Content-Type": "application/json"},
    json={
        "aliases": [
            {"address": "info@acme.com", "goto": "admin@acme.com"},
            {"address": "support@acme.com", "goto": "jane@acme.com"},
        ]
    },
)
print(f"Aliases created: {resp.json()['data']['summary']}")

print("Setup complete!")
```
