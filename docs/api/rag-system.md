# RAG System

AI-powered semantic search for emails using Retrieval-Augmented Generation (RAG).
Emails are converted to vector embeddings and stored in a Qdrant vector database
for meaning-based search.

All RAG gateway routes live under the `/api/v1/rag` prefix. Domain- and
organization-scoped routes verify ownership: a tenant credential can only address
its own domains and organization (`404` otherwise); platform scope can address any.

!!! note "Gateway/service mismatch fixed 2026-08-30"
    Every route on this page is now backed by the RAG service. The gateway
    forwards organization config/stats/reindex to the service's existing
    organization API, health to its health API, and the rest (search,
    collections, documents, index status/trigger, domain config) to a
    gateway-compatibility router added to the service
    (`worker/rag/api/gateway_api.py`). Domain- and organization-scoped
    forwards carry the domain's owning `organization_id`, and the service
    scopes every vector operation to that organization's collection.
    `503` (`{"error": "RAG service unavailable"}`) still means the RAG
    container itself is unreachable.

## Semantic Search

Search emails using natural language.

```
POST /api/v1/rag/search
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `query` | string | Yes | Natural language search query |
| `organization_id` | string | No | **Ignored for tenant credentials** -- always forced to the caller's own organization. Platform-scoped callers may set it (defaults to `default`) |
| `domain` | string | No | Limit search to a specific domain |
| `limit` | integer | No | Max results to return |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "invoices from last quarter", "domain": "acme.com", "limit": 5}' \
  http://your-server:8083/api/v1/rag/search
```

## Indexing

### Get Index Status

```
GET /api/v1/rag/rag/index/status/{domain}
```

(The doubled `rag` segment is real: the module mounts at `/api/v1/rag` and the
route path is `/rag/index/status/{domain}`.)

### Trigger Indexing

```
POST /api/v1/rag/rag/index/trigger
```

The JSON body is forwarded to the RAG service with `organization_id` forced to
the caller's own organization (tenant credentials).

!!! note "No background indexing pipeline yet"
    The trigger delegates to the service's reindex operation, which is a
    tracked-debt stub (`plans/03-mailyte-api/tracked-debt.md#3`): it responds
    with an acceptance shape but queues no work. Real content reaches the
    vector store synchronously through [Add Documents](#add-documents).

## Collections

### List Collections for a Domain

```
GET /api/v1/rag/collections/{domain}
```

### List Collections for an Organization

```
GET /api/v1/rag/organizations/{organization_id}/collections
```

### Create Collection

```
POST /api/v1/rag/collections/{domain}
```

The JSON body is forwarded to the RAG service as-is.

### Collection Statistics

```
GET /api/v1/rag/collections/{domain}/stats
```

## Documents

### Add Documents

```
POST /api/v1/rag/documents/{collection_id}
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `documents` | array | Yes | Objects of `{"content": "...", "metadata": {...}}` |
| `domain` | string | No | Stamped onto each stored vector for domain-filtered search |
| `organization_id` | string | No | **Ignored for tenant credentials** -- forced to the caller's own organization |

Documents are embedded and stored synchronously; the response reports
`documents_indexed` and the vector `point_ids`.

!!! note "Ownership gap closed 2026-08-30"
    Collection names carry the owning organization
    (`{prefix}_{org}_{type}`), and the RAG service now rejects (`404`) any
    `collection_id` that does not belong to the `organization_id` the gateway
    forces into the body -- a tenant can no longer address another
    organization's collection.

## Domain RAG Configuration

```
GET  /api/v1/rag/rag/config/domain/{domain}
POST /api/v1/rag/rag/config/domain/{domain}
```

## Organization RAG Configuration

```
GET /api/v1/rag/organizations/{organization_id}/rag/config
PUT /api/v1/rag/organizations/{organization_id}/rag/config
```

The PUT is a **full-config replace** (that is what the RAG service's config
write implements): the body must carry the complete organization config
shape -- `enabled_fields` is required -- and partial bodies are rejected with
`422`. Domain-level config (`/rag/config/domain/{domain}`) applies at the
organization level too; there is no per-domain override store yet.

### Organization RAG Statistics

```
GET /api/v1/rag/organizations/{organization_id}/rag/stats
```

### Organization RAG Documents

```
GET /api/v1/rag/organizations/{organization_id}/rag/documents
```

Query parameters (pagination/filtering) are forwarded to the RAG service.

### Trigger Organization Reindex

```
POST /api/v1/rag/organizations/{organization_id}/rag/reindex
```

## RAG Health

```
GET /api/v1/rag/health
```

## Errors

| Status | Meaning |
|---|---|
| `400` | Invalid configuration values (e.g. unknown indexing field, chunk overlap >= chunk size) |
| `404` | Domain/organization not found or not owned by your organization, or a `collection_id` that does not belong to your organization |
| `422` | Body failed validation on the RAG service (e.g. partial org config on the PUT) |
| `503` | RAG service unreachable (`{"error": "RAG service unavailable"}`), or its embedding backend is down |
