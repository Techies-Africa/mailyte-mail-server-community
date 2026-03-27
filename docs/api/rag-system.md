# RAG System

> **Enterprise Edition** — This feature is available in [Mailyte Enterprise](https://mailyte.com). The Community Edition does not include this functionality.


AI-powered semantic search for emails using Retrieval-Augmented Generation (RAG). Emails are converted to vector embeddings and stored in a Qdrant vector database for fast, meaning-based search.

## Semantic Search

Search emails using natural language. The query is converted to a vector embedding and matched against indexed emails by meaning, not just keywords.

```
POST /api/v1/rag/search
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `query` | string | Yes | Natural language search query |
| `organization_id` | string | No | Organization scope (auto-detected from API key if not set) |
| `domain` | string | No | Limit search to a specific domain |
| `limit` | integer | No | Max results to return (default: 10) |
| `min_score` | float | No | Minimum similarity score (0.0 to 1.0) |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "invoices from last quarter",
    "organization_id": "acme",
    "domain": "acme.com",
    "limit": 5,
    "min_score": 0.7
  }' \
  http://your-server:5000/api/v1/rag/search
```

**Example Response**

```json
{
  "results": [
    {
      "email_id": "msg_abc123",
      "subject": "Q4 Invoice #1234",
      "from": "billing@vendor.com",
      "to": "accounting@acme.com",
      "date": "2025-01-15T10:30:00",
      "snippet": "Please find attached the invoice for Q4 services...",
      "score": 0.92
    },
    {
      "email_id": "msg_def456",
      "subject": "Invoice Reminder - December",
      "from": "accounts@supplier.com",
      "to": "finance@acme.com",
      "date": "2025-01-05T08:00:00",
      "snippet": "This is a reminder for the outstanding invoice...",
      "score": 0.85
    }
  ],
  "total": 2,
  "query": "invoices from last quarter"
}
```

## Collections

### List Collections for a Domain

Get all RAG collections (vector stores) for a domain.

```
GET /api/v1/collections/{domain}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/collections/acme.com
```

### List Collections for an Organization

Get all RAG collections across all domains in an organization.

```
GET /api/v1/organizations/{organization_id}/collections
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/organizations/acme/collections
```

### Create Collection

Create a new RAG collection for a domain.

```
POST /api/v1/collections/{domain}
```

**Request Body**

| Field | Type | Description |
|---|---|---|
| `name` | string | Collection name |
| `description` | string | Description |
| `embedding_model` | string | Embedding model to use |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "acme-emails",
    "description": "All email embeddings for acme.com",
    "embedding_model": "text-embedding-3-small"
  }' \
  http://your-server:5000/api/v1/collections/acme.com
```

### Collection Statistics

Get statistics for a domain's collections (document count, storage used).

```
GET /api/v1/collections/{domain}/stats
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/collections/acme.com/stats
```

## Documents

### Add Documents

Add documents (email content) to a collection for indexing.

```
POST /api/v1/documents/{collection_id}
```

**Request Body**

```json
{
  "documents": [
    {
      "id": "msg_abc123",
      "content": "Email body text here...",
      "metadata": {
        "subject": "Q4 Invoice",
        "from": "billing@vendor.com",
        "date": "2025-01-15T10:30:00"
      }
    }
  ]
}
```

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "documents": [
      {
        "id": "msg_abc123",
        "content": "Please find attached the invoice for Q4 services totaling $15,000.",
        "metadata": {"subject": "Q4 Invoice", "from": "billing@vendor.com"}
      }
    ]
  }' \
  http://your-server:5000/api/v1/documents/collection_1
```

## Indexing

### Get Index Status

Check the indexing status for a domain.

```
GET /api/v1/rag/index/status/{domain}
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/rag/index/status/acme.com
```

**Example Response**

```json
{
  "domain": "acme.com",
  "status": "completed",
  "total_emails": 15000,
  "indexed_emails": 15000,
  "last_indexed_at": "2025-03-25T02:00:00",
  "next_scheduled_at": "2025-03-26T02:00:00"
}
```

### Trigger Indexing

Manually trigger email indexing for a domain or organization.

```
POST /api/v1/rag/index/trigger
```

**Request Body**

| Field | Type | Description |
|---|---|---|
| `domain` | string | Domain to index |
| `full_reindex` | boolean | Reindex everything (not just new emails) |

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"domain": "acme.com", "full_reindex": false}' \
  http://your-server:5000/api/v1/rag/index/trigger
```

## Organization RAG Configuration

### Get Configuration

```
GET /api/v1/organizations/{organization_id}/rag/config
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/organizations/acme/rag/config
```

**Example Response**

```json
{
  "organization_id": "acme",
  "config": {
    "enabled": true,
    "embedding_provider": "openai",
    "embedding_model": "text-embedding-3-small",
    "auto_index": true,
    "index_schedule": "0 2 * * *",
    "max_documents": 100000
  }
}
```

### Update Configuration

```
PUT /api/v1/organizations/{organization_id}/rag/config
```

**Request Body**

| Field | Type | Description |
|---|---|---|
| `enabled` | boolean | Enable or disable RAG for the organization |
| `embedding_provider` | string | Provider: `openai`, `azure_openai`, `huggingface`, `sentence_transformers` |
| `embedding_model` | string | Model name |
| `auto_index` | boolean | Automatically index new emails |
| `index_schedule` | string | Cron expression for scheduled indexing |

**Example Request**

```bash
curl -X PUT -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "embedding_provider": "openai",
    "embedding_model": "text-embedding-3-small",
    "auto_index": true
  }' \
  http://your-server:5000/api/v1/organizations/acme/rag/config
```

### Organization RAG Statistics

```
GET /api/v1/organizations/{organization_id}/rag/stats
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/organizations/acme/rag/stats
```

### Organization RAG Documents

List indexed documents for an organization.

```
GET /api/v1/organizations/{organization_id}/rag/documents
```

**Query Parameters**

| Parameter | Type | Description |
|---|---|---|
| `page` | integer | Page number |
| `per_page` | integer | Items per page |

### Trigger Organization Reindex

Reindex all emails for an organization.

```
POST /api/v1/organizations/{organization_id}/rag/reindex
```

**Example Request**

```bash
curl -X POST -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{}' \
  http://your-server:5000/api/v1/organizations/acme/rag/reindex
```

## RAG Health

Check the health status of the RAG service and Qdrant connection.

```
GET /api/v1/rag/health
```

**Example Request**

```bash
curl -H "X-API-Key: YOUR_KEY" \
  http://your-server:5000/api/v1/rag/health
```

**Example Response**

```json
{
  "status": "healthy",
  "qdrant": "connected",
  "embedding_provider": "openai",
  "collections": 5,
  "total_documents": 75000
}
```
