---
edition: enterprise
---

# White Label

Per-organization branding: custom logo, colors, brand domain, login-page and footer HTML, support contacts, and CSS overrides for the web panel.

**Base path:** `/api/v1/whitelabel`
**Auth:** every endpoint requires **platform scope with an operator session at role `admin` or above** (`require_api_key("admin", scope="platform", role="admin")`). Tenant API keys cannot manage white-label configuration.

## The config object

| Field | Type | Description |
|---|---|---|
| `logo_url` | string \| null | URL to the organization's logo image |
| `primary_color` | string \| null | Primary brand color (hex, e.g. `#3B82F6`) |
| `secondary_color` | string \| null | Secondary brand color (hex) |
| `brand_name` | string \| null | Brand display name shown in the UI |
| `custom_domain` | string \| null | Custom CNAME domain for the web panel |
| `login_page_html` | string \| null | Custom HTML snippet for the login page |
| `footer_html` | string \| null | Custom HTML snippet for the page footer |
| `favicon_url` | string \| null | URL to a custom favicon |
| `support_email` | string \| null | Branded support email address |
| `support_url` | string \| null | URL to a branded support / help-desk page |
| `custom_css` | string \| null | Custom CSS overrides applied to the web panel |

## Get White-Label Config

Returns the branding configuration for an organization.

```
GET /api/v1/whitelabel/config/{org_id}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  http://your-server:5000/api/v1/whitelabel/config/01J8ZJXQ2W3E4R5T6Y7U8I9O0P
```

**Example Response**

```json
{
  "organization_id": "01J8ZJXQ2W3E4R5T6Y7U8I9O0P",
  "whitelabel": {
    "brand_name": "Acme Mail",
    "logo_url": "https://cdn.acme.com/logo.png",
    "primary_color": "#3B82F6",
    "custom_domain": "mail.acme.com"
  },
  "updated_at": "2026-08-15T10:30:00"
}
```

## Update White-Label Config

Merge-update the branding configuration. Only fields included in the request body are changed; omitted fields retain their current values.

```
PUT /api/v1/whitelabel/config/{org_id}
```

**Request Body:** any subset of [the config object](#the-config-object).

**Example Request**

```bash
curl -X PUT -H "X-API-Key: YOUR_PLATFORM_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "brand_name": "Acme Mail",
    "primary_color": "#3B82F6",
    "custom_domain": "mail.acme.com"
  }' \
  http://your-server:5000/api/v1/whitelabel/config/01J8ZJXQ2W3E4R5T6Y7U8I9O0P
```

Returns the merged config in the same shape as [Get](#get-white-label-config).

## Verify Custom Brand Domain

Verify that the organization's custom white-label domain has a valid CNAME DNS record pointing to the platform. The `custom_domain` must be saved in the config first.

```
POST /api/v1/whitelabel/config/{org_id}/verify-domain
```

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_PLATFORM_KEY" \
  http://your-server:5000/api/v1/whitelabel/config/01J8ZJXQ2W3E4R5T6Y7U8I9O0P/verify-domain
```

**Example Response**

```json
{
  "organization_id": "01J8ZJXQ2W3E4R5T6Y7U8I9O0P",
  "custom_domain": "mail.acme.com",
  "verified": true,
  "resolved_cname": "panel.mailyte.com.",
  "message": "CNAME verified"
}
```

## Preview Branded Login Page

Return a preview of the branded login page built from the organization's white-label settings. The response contains an HTML snippet inside a JSON envelope.

```
GET /api/v1/whitelabel/config/{org_id}/preview
```

```bash
curl -H "X-API-Key: YOUR_PLATFORM_KEY" \
  http://your-server:5000/api/v1/whitelabel/config/01J8ZJXQ2W3E4R5T6Y7U8I9O0P/preview
```

## Remove White-Label Config

Remove the white-label configuration for an organization, reverting it to the platform's default branding.

```
DELETE /api/v1/whitelabel/config/{org_id}
```

```bash
curl -X DELETE -H "X-API-Key: YOUR_PLATFORM_KEY" \
  http://your-server:5000/api/v1/whitelabel/config/01J8ZJXQ2W3E4R5T6Y7U8I9O0P
```

!!! danger "Irreversible"
    Deleting the config discards all stored branding. There is no undo — re-create the configuration to restore branding.
