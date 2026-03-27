# JavaScript Examples

JavaScript examples using `fetch` (browser/Node.js 18+) and `axios`.

## Setup with fetch

```javascript
const MAILYTE_URL = "http://your-server:5000";
const MAILYTE_KEY = "mk_live_your_api_key_here";

async function mailyte(method, path, body = null) {
  const options = {
    method,
    headers: {
      "X-API-Key": MAILYTE_KEY,
      "Content-Type": "application/json",
    },
  };

  if (body) {
    options.body = JSON.stringify(body);
  }

  const resp = await fetch(`${MAILYTE_URL}${path}`, options);
  const data = await resp.json();

  if (!resp.ok || data.type === "error") {
    throw new Error(`${resp.status}: ${data.msg}`);
  }

  return data;
}
```

## Setup with axios

```javascript
const axios = require("axios");

const client = axios.create({
  baseURL: "http://your-server:5000",
  headers: {
    "X-API-Key": "mk_live_your_api_key_here",
    "Content-Type": "application/json",
  },
});

// Response interceptor for error handling
client.interceptors.response.use(
  (resp) => {
    if (resp.data.type === "error") {
      const err = new Error(resp.data.msg);
      err.response = resp;
      throw err;
    }
    return resp.data;
  },
  (error) => {
    const msg = error.response?.data?.msg || error.message;
    throw new Error(`${error.response?.status}: ${msg}`);
  }
);
```

## Organization Examples

### Create an Organization

=== "fetch"

    ```javascript
    const org = await mailyte("POST", "/api/v1/organizations", {
      id: "acme",
      name: "Acme Corp",
      admin_email: "admin@acme.com",
      rate_limits: {
        inbound_hourly: 5000,
        outbound_hourly: 2000,
      },
      storage_quotas: {
        storage_quota_mb: 51200,
      },
    });

    console.log("Created:", org.data.id);
    ```

=== "axios"

    ```javascript
    const { data } = await client.post("/api/v1/organizations", {
      id: "acme",
      name: "Acme Corp",
      admin_email: "admin@acme.com",
      rate_limits: {
        inbound_hourly: 5000,
        outbound_hourly: 2000,
      },
    });

    console.log("Created:", data.data.id);
    ```

### List Organizations

=== "fetch"

    ```javascript
    const orgs = await mailyte("GET", "/api/v1/organizations?page=1&per_page=25");

    for (const org of orgs.data.items) {
      console.log(`${org.id}: ${org.name} (${org.domain_count} domains)`);
    }
    ```

=== "axios"

    ```javascript
    const { data } = await client.get("/api/v1/organizations", {
      params: { page: 1, per_page: 25 },
    });

    for (const org of data.data.items) {
      console.log(`${org.id}: ${org.name} (${org.domain_count} domains)`);
    }
    ```

## Domain Examples

### Add a Domain

```javascript
const domain = await mailyte("POST", "/api/v1/domains", {
  domain: "acme.com",
  organization_id: "acme",
  max_quota: 10737418240,
  max_users: 500,
  dkim_enabled: true,
});

console.log(`Domain created: ${domain.data.domain} (ID: ${domain.data.id})`);
```

### List Domains for an Organization

```javascript
const domains = await mailyte(
  "GET",
  "/api/v1/domains?organization_id=acme"
);

for (const d of domains.data.items) {
  console.log(`${d.domain} - ${d.email_account_count} accounts`);
}
```

## Email Account Examples

### Create an Account

```javascript
const account = await mailyte("POST", "/api/v1/email-accounts", {
  email: "john@acme.com",
  password: "SecurePass123",
  name: "John Doe",
  storage_quota: 2147483648,
});

console.log(`Created: ${account.data.email} (ID: ${account.data.id})`);
```

### Create Multiple Accounts

```javascript
const users = [
  { email: "john@acme.com", password: "SecurePass123", name: "John Doe" },
  { email: "jane@acme.com", password: "SecurePass456", name: "Jane Smith" },
  { email: "admin@acme.com", password: "AdminPass789", name: "Admin" },
];

for (const user of users) {
  try {
    const result = await mailyte("POST", "/api/v1/email-accounts", user);
    console.log(`Created: ${result.data.email}`);
  } catch (err) {
    console.error(`Failed: ${user.email} - ${err.message}`);
  }
}
```

### List Accounts with Pagination

```javascript
async function getAllAccounts(orgId) {
  const accounts = [];
  let page = 1;

  while (true) {
    const result = await mailyte(
      "GET",
      `/api/v1/email-accounts?organization_id=${orgId}&page=${page}`
    );

    accounts.push(...result.data.items);

    if (page >= result.data.pagination.total_pages) break;
    page++;
  }

  return accounts;
}

const all = await getAllAccounts("acme");
console.log(`Total accounts: ${all.length}`);
```

## Webhook Examples

### Set Up a Webhook

```javascript
const webhook = await mailyte("POST", "/api/v1/webhooks/endpoints", {
  url: "https://hooks.acme.com/mailyte",
  description: "Production events",
  event_types: ["email.sent", "email.delivered", "email.bounced"],
});

// Store this secret securely
console.log(`Webhook secret: ${webhook.data.secret}`);

// Test it
await mailyte("POST", `/api/v1/webhooks/endpoints/${webhook.data.id}/test`);
console.log("Test event sent");
```

### Check Failed Deliveries

```javascript
const failures = await mailyte(
  "GET",
  "/api/v1/webhooks/deliveries?status=FAILED"
);

for (const f of failures.data.items) {
  console.log(`${f.event_type} -> ${f.webhook_url}: ${f.error_message}`);
}
```

## RAG Search Example

```javascript
const results = await mailyte("POST", "/api/v1/rag/search", {
  query: "contract renewal documents from January",
  organization_id: "acme",
  limit: 5,
});

for (const r of results.results) {
  console.log(`[${r.score.toFixed(2)}] ${r.subject} - from ${r.from}`);
}
```

## Health Check

```javascript
const resp = await fetch(`${MAILYTE_URL}/health`);
const health = await resp.json();
console.log(`Status: ${health.status}, DB: ${health.database}`);
```

## Error Handling

```javascript
try {
  await mailyte("POST", "/api/v1/organizations", {
    id: "acme",
    name: "Acme Corp",
  });
} catch (err) {
  const message = err.message;

  if (message.startsWith("409:")) {
    console.log("Organization already exists");
  } else if (message.startsWith("400:")) {
    console.log("Validation error:", message);
  } else if (message.startsWith("401:")) {
    console.log("Check your API key");
  } else {
    console.error("Unexpected error:", message);
  }
}
```

## Full Setup Script (Node.js)

```javascript
async function setupOrganization() {
  console.log("=== Creating organization ===");
  await mailyte("POST", "/api/v1/organizations", {
    id: "acme",
    name: "Acme Corp",
    admin_email: "admin@acme.com",
  });

  console.log("=== Adding domain ===");
  await mailyte("POST", "/api/v1/domains", {
    domain: "acme.com",
    organization_id: "acme",
  });

  console.log("=== Creating email accounts ===");
  for (const name of ["john", "jane", "admin"]) {
    await mailyte("POST", "/api/v1/email-accounts", {
      email: `${name}@acme.com`,
      password: "SecurePass123",
      name: name.charAt(0).toUpperCase() + name.slice(1),
    });
    console.log(`  Created ${name}@acme.com`);
  }

  console.log("=== Setting up webhook ===");
  const wh = await mailyte("POST", "/api/v1/webhooks/endpoints", {
    url: "https://hooks.acme.com/mailyte",
    event_types: ["email.delivered", "email.bounced"],
  });
  console.log(`  Webhook secret: ${wh.data.secret}`);

  console.log("=== Done! ===");
}

setupOrganization().catch(console.error);
```
