# API Documentation

The API reference lives in [docs/api/](api/index.md), regenerated against the route code on 2026-08-30.

Start with:

- [API Overview & Quickstart](api/index.md) — base URLs, auth, error envelope
- [Authentication](api/authentication.md) — the four credential types
- [Full Endpoint Index](reference/api-endpoints.md) — all 307 endpoints across 32 route modules

This file exists only as a pointer: `docker-compose.dev.yml` bind-mounts it into the docs container, and if it is deleted Docker recreates it as an empty directory on the next `up`.
