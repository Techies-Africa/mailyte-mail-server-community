# Kubernetes Deployment

**Mailyte does not currently support Kubernetes.** The repository ships no Kubernetes manifests, no Helm charts, and no container images published for cluster consumption — and this page previously described manifests that never existed. Docker Compose on a single host is the only supported deployment model.

## Why not (yet)

Several load-bearing pieces of the stack assume the single-host Compose topology:

- **Almost everything builds from source.** Only a handful of services use registry images; the rest are `build:` contexts. There is no published image set a cluster could pull.
- **Shared bind mounts.** Postfix, Dovecot, and storage_usage share `storage/mail_data`; cert_manager, Traefik, Postfix, and Dovecot share `storage/ssl_certs` and the SNI map. These are same-host filesystem contracts, not volumes that can be scheduled across nodes without a redesign around RWX storage.
- **Singleton statefulness.** There is exactly one Postfix (with its spool volume), one Dovecot, one MySQL. Running two of them against the same data and ports is actively unsafe, so most of the stack cannot be horizontally scheduled anyway.
- **Docker-API coupling.** The `monitoring` service restarts unhealthy containers and `cert_manager` reloads Postfix/Dovecot through a scoped Docker socket proxy (`docker-proxy`). Both mechanisms are Docker-specific.
- **Compose-native gating.** Startup ordering (secrets validation → MySQL → Alembic `migrate` → everything else) is enforced with `depends_on: service_completed_successfully`, which has no direct Kubernetes equivalent without rewriting it as init containers and Jobs.

## What to do instead

- **Scale vertically first.** A single well-sized host handles thousands of mailboxes — see the [Scaling Guide](scaling-guide.md) and [Requirements](requirements.md).
- **Offload the databases.** `docker-compose.cloud.yml` runs the stack against a managed MySQL and Redis (RDS, ElastiCache, ...), which removes the largest stateful components from the host.
- **Scale the stateless HTTP tier.** `docker-compose.prod.yml` already runs `api`, `webhooks`, and `tracking` at 2 replicas behind Traefik on the same host.

If you build a Kubernetes port anyway, treat it as a fork-level engineering project — budget for a storage redesign (RWX mail volumes), a replacement for the docker-proxy reload/restart paths, and a migration Job wired in front of every schema-dependent Deployment.

## Related

- [Docker Deployment](docker-deployment.md) — the supported model, in detail
- [Production Setup](production-setup.md) — zero to production on one host
- [Scaling Guide](scaling-guide.md) — what actually scales, and how
