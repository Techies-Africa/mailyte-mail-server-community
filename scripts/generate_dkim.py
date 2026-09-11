#!/usr/bin/env python3
"""
DKIM key sync -- host-side orchestrator.

The actual DKIM logic lives in worker/api/utils/dkim_sync.py and runs inside
the `api` container (the only service with MySQL access AND the envelope-
encryption KEK). This script is the docker-host glue that this repo's layout
makes necessary:

  - MySQL is not published on the host, so nothing here talks to the DB;
  - the rspamd image ships no Python, so nothing runs in that container
    beyond bash;
  - only the rspamd container mounts storage/dkim_keys AND the baked
    /etc/rspamd/dkim_selectors.map, so the files must be materialised inside
    it (docker-compose.yml documents this on the rspamd service).

So this wrapper asks the api container for the desired state (decrypted in
the api process, streamed over the docker exec pipe -- never written to the
host disk, never passed via argv/env), then writes each key file and the
selector map inside the rspamd container, owned by _rspamd with 0600/0644
modes. Rspamd picks up new key files on first use (the signing path template
is resolved per message) and re-reads the selector map on its own map watch
interval; --reload forces it via SIGHUP (brief restart through supervisord).

Usage (run on the docker host, stack running):
    python3 scripts/generate_dkim.py sync [--prune] [--reload]
    python3 scripts/generate_dkim.py backfill            # mint keys for keyless domains + sync
    python3 scripts/generate_dkim.py dns <domain>        # print the DNS TXT record
    ./start.sh dkim [args...]                            # same thing via mailyte-ctl

Key generation/rotation for a domain that already has a key is deliberately
NOT here: use the API's two-step flow (POST /api/v1/domains/{id}/dkim/rotate,
then .../dkim/{selector}/activate) so signing never moves to a selector whose
DNS record has not propagated.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

def _resolve_container(env_var, *patterns):
    """The container name to exec into.

    A bare compose stack names the service 'api'/'rspamd', but a project-scoped
    deploy with replicas names it 'mailyte-prod-api-1' etc., so the old
    hardcoded 'api' could never be exec'd in production (DKIM keys were stored
    in the DB but never materialised into rspamd -- found 2026-09-06). Honor an
    explicit override first, then the literal name, then the first RUNNING
    container whose name contains a pattern (so replicas resolve automatically).
    """
    override = os.getenv(env_var)
    if override:
        return override
    docker = shutil.which("docker") or "docker"
    try:
        running = subprocess.run(
            [docker, "ps", "--format", "{{.Names}}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        ).stdout.split()
    except Exception:
        running = []
    for pat in patterns:
        if pat in running:  # exact name wins (e.g. 'rspamd')
            return pat
    for pat in patterns:
        for name in running:
            # a service token, not a substring of an unrelated name
            if name == pat or f"-{pat}-" in name or name.endswith(f"-{pat}") or name.startswith(f"{pat}-"):
                return name
    return patterns[0]  # fall back to the literal; _check_containers reports clearly


API_CONTAINER = _resolve_container("MAILYTE_API_CONTAINER", "api")
RSPAMD_CONTAINER = _resolve_container("MAILYTE_RSPAMD_CONTAINER", "rspamd")
RSPAMD_KEY_DIR = "/var/lib/rspamd/dkim"
RSPAMD_MAP_PATHS = (
    # The path dkim_signing.conf actually reads. Baked into the image layer,
    # so it resets on every container recreate -- re-run `sync` after deploys.
    "/etc/rspamd/dkim_selectors.map",
    # Persistent copy on the shared storage/dkim_keys mount, so the host and
    # future config (selector_map pointed here) keep a durable version.
    f"{RSPAMD_KEY_DIR}/dkim_selectors.map",
)

# Mirrors utils/dkim_sync.py: domain = dot-separated LDH labels, selector =
# a single label with no dots. Both become path components inside rspamd.
_DOMAIN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$", re.I
)
_SELECTOR_RE = re.compile(r"^[a-z0-9]([a-z0-9_-]{0,61}[a-z0-9])?$", re.I)

# Exit codes utils/dkim_sync.py's CLI uses
EXIT_KEY_DIR_UNAVAILABLE = 3


def _docker():
    for candidate in ("docker",):
        if shutil.which(candidate):
            return candidate
    sys.exit("error: docker CLI not found on PATH -- this script runs on the docker host")


def _exec(container, argv, input_bytes=None, capture=True):
    """docker exec into a running container. Returns CompletedProcess."""
    cmd = [_docker(), "exec", "-i", container] + argv
    return subprocess.run(
        cmd,
        input=input_bytes,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE,
    )


def _check_containers():
    for name in (API_CONTAINER, RSPAMD_CONTAINER):
        probe = subprocess.run(
            [_docker(), "exec", name, "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if probe.returncode != 0:
            sys.exit(
                f"error: cannot exec into the '{name}' container -- is the stack running? "
                "(./start.sh status)"
            )


def _module(args, input_bytes=None):
    """Run python -m utils.dkim_sync <args> inside the api container."""
    return _exec(API_CONTAINER, ["python", "-m", "utils.dkim_sync"] + args, input_bytes)


def _safe_pair(domain, selector):
    return bool(_DOMAIN_RE.match(domain or "") and _SELECTOR_RE.match(selector or ""))


def _write_key_in_rspamd(domain, selector, pem):
    """Write one key file inside the rspamd container: 0600, _rspamd-owned,
    atomic rename. The PEM travels only over the exec stdin pipe."""
    # Two ordering hazards, both because the rspamd container has CAP_CHOWN but
    # NOT CAP_FOWNER, so uid-0 can chmod only files it OWNS:
    #   1. chmod must come BEFORE chown -- once the tmp is chowned to _rspamd,
    #      root can no longer chmod it (EPERM). Set the mode while root owns it.
    #   2. rm any stale .tmp first -- a half-finished prior run leaves an
    #      _rspamd-owned .tmp that `cat >` truncates without re-owning, so the
    #      writer would again be chmod-ing a file it does not own.
    script = (
        "set -euo pipefail; umask 077; "
        f'f="{RSPAMD_KEY_DIR}/$1.$2.key"; '
        'rm -f "$f.tmp"; cat > "$f.tmp"; chmod 0600 "$f.tmp"; chown _rspamd:_rspamd "$f.tmp"; '
        'mv -f "$f.tmp" "$f"'
    )
    result = _exec(
        RSPAMD_CONTAINER,
        ["bash", "-c", script, "dkim-sync", domain, selector],
        input_bytes=pem.encode(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"writing {domain}.{selector}.key failed: {result.stderr.decode().strip()}"
        )


def _write_map_in_rspamd(map_content):
    for path in RSPAMD_MAP_PATHS:
        # Same CAP_FOWNER constraint as _write_key_in_rspamd: chmod BEFORE
        # chown (root can only chmod a file it owns), and clear any stale tmp
        # so the writer always owns it.
        script = (
            "set -euo pipefail; umask 022; "
            f'rm -f "{path}.tmp"; cat > "{path}.tmp"; chmod 0644 "{path}.tmp"; '
            f'chown _rspamd:_rspamd "{path}.tmp" 2>/dev/null || true; mv -f "{path}.tmp" "{path}"'
        )
        result = _exec(RSPAMD_CONTAINER, ["bash", "-c", script], input_bytes=map_content.encode())
        if result.returncode != 0:
            raise RuntimeError(f"writing {path} failed: {result.stderr.decode().strip()}")


def _fix_ownership():
    """Chown any key file the api container's write-through left behind (it
    writes as uid 10001; rspamd reads as _rspamd)."""
    script = (
        f'find {RSPAMD_KEY_DIR} -maxdepth 1 -name "*.key" '
        "-exec chown _rspamd:_rspamd {} + -exec chmod 0600 {} + 2>/dev/null || true"
    )
    _exec(RSPAMD_CONTAINER, ["bash", "-c", script])


def _list_rspamd_keys():
    result = _exec(RSPAMD_CONTAINER, ["bash", "-c", f"ls -1 {RSPAMD_KEY_DIR} 2>/dev/null || true"])
    names = []
    for name in result.stdout.decode().splitlines():
        name = name.strip()
        if not name.endswith(".key"):
            continue
        body = name[: -len(".key")]
        domain, _, selector = body.rpartition(".")
        if domain and _safe_pair(domain, selector):
            names.append((domain, selector))
    return names


def _export_state():
    result = _module(["export", "--with-private-keys"])
    if result.returncode != 0:
        sys.exit(
            "error: exporting DKIM state from the api container failed:\n"
            + result.stderr.decode().strip()
        )
    try:
        return json.loads(result.stdout.decode())
    except json.JSONDecodeError:
        sys.exit("error: unexpected output from utils.dkim_sync export")


def cmd_sync(prune=False, reload_rspamd=False):
    _check_containers()
    state = _export_state()

    desired = set()
    written = 0
    for key in state["keys"]:
        domain, selector = key["domain"], key["selector"]
        if not _safe_pair(domain, selector):
            print(f"  skipping unsafe pair {domain!r}/{selector!r}", file=sys.stderr)
            continue
        _write_key_in_rspamd(domain, selector, key["private_key_pem"])
        desired.add((domain, selector))
        written += 1

    removed = 0
    if prune:
        for domain, selector in _list_rspamd_keys():
            if (domain, selector) in desired:
                continue
            _exec(
                RSPAMD_CONTAINER,
                ["rm", "-f", "--", f"{RSPAMD_KEY_DIR}/{domain}.{selector}.key"],
            )
            removed += 1

    _write_map_in_rspamd(state["map_content"])
    _fix_ownership()

    for line in state.get("skipped", []):
        print(f"  skipped (no exportable key material): {line}", file=sys.stderr)

    print(
        f"DKIM sync complete: {written} key file(s) written, {removed} pruned, "
        f"selector map has {state['map_entries']} entr(y/ies)."
    )
    print(
        "Rspamd loads new key files on first use and re-reads the selector map on its "
        "map watch interval (about a minute)."
    )

    if reload_rspamd:
        result = subprocess.run(
            [_docker(), "kill", "--signal=HUP", RSPAMD_CONTAINER],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if result.returncode == 0:
            print(
                "Sent SIGHUP to rspamd (supervisord restarts its programs -- expect a "
                "few seconds of scanning downtime)."
            )
        else:
            print(f"warning: SIGHUP failed: {result.stderr.decode().strip()}", file=sys.stderr)


def cmd_backfill(prune=False, reload_rspamd=False):
    _check_containers()
    result = _module(["generate-missing", "--json"])
    # exit 3 = keys were minted/stored, but the api container has no key dir
    # mounted -- expected; the sync below materialises the files via rspamd.
    if result.returncode not in (0, EXIT_KEY_DIR_UNAVAILABLE):
        sys.exit(
            "error: generate-missing failed inside the api container:\n"
            + result.stderr.decode().strip()
        )
    try:
        created = json.loads(result.stdout.decode()).get("created", [])
    except json.JSONDecodeError:
        created = []

    if created:
        print(f"Minted {len(created)} new DKIM key(s). Publish these DNS records:")
        for record in created:
            print(f"\n  {record['domain']} (selector {record['selector']}):")
            print(f'  {record["record_name"]} IN TXT "{record["record_value"]}"')
        print()
    else:
        print("All active dkim_enabled domains already have DKIM keys.")

    cmd_sync(prune=prune, reload_rspamd=reload_rspamd)


def cmd_dns(domain):
    _check_containers()
    result = _module(["dns", domain])
    sys.stdout.write(result.stdout.decode())
    sys.stderr.write(result.stderr.decode())
    sys.exit(result.returncode)


ROTATE_GUIDANCE = (
    "Per-domain generation/rotation moved to the API so it can be done without a\n"
    "no-DNS-yet signing window:\n"
    "  1. POST /api/v1/domains/{domain_id}/dkim/rotate   -> mints a key under a new selector\n"
    "  2. publish the returned TXT record, wait for propagation\n"
    "  3. POST /api/v1/domains/{domain_id}/dkim/{selector}/activate\n"
    "The API write-through (plus `generate_dkim.py sync`) exports the files to rspamd.\n"
    "For domains that have NO key at all, `generate_dkim.py backfill` mints one safely."
)


def main():
    parser = argparse.ArgumentParser(
        description="Sync DKIM keys from MySQL to rspamd (host-side orchestrator).",
        epilog="Legacy flags (--all, --update-map, --rotate, --dns) are mapped or refused "
        "with guidance; see the module docstring.",
    )
    parser.add_argument(
        "command", nargs="?", default="sync", help="sync (default) | backfill | dns <domain>"
    )
    parser.add_argument("domain", nargs="?", help="domain for the dns command")
    parser.add_argument(
        "--prune",
        action="store_true",
        help="delete key files that no longer match an active, dkim-enabled domain",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="SIGHUP the rspamd container afterwards (brief restart; only needed "
        "if an existing key file was replaced in place)",
    )
    # Legacy compatibility
    parser.add_argument("--all", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--update-map", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--rotate", metavar="DOMAIN", help=argparse.SUPPRESS)
    parser.add_argument("--rotate-all", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dns", metavar="DOMAIN", help=argparse.SUPPRESS)
    parser.add_argument("--selector", help=argparse.SUPPRESS)
    parser.add_argument("--key-size", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.rotate or args.rotate_all:
        sys.exit(ROTATE_GUIDANCE)
    if args.dns:
        return cmd_dns(args.dns)
    if args.all:
        return cmd_backfill(prune=args.prune, reload_rspamd=args.reload)
    if args.update_map:
        return cmd_sync(prune=args.prune, reload_rspamd=args.reload)

    if args.command == "sync":
        return cmd_sync(prune=args.prune, reload_rspamd=args.reload)
    if args.command == "backfill":
        return cmd_backfill(prune=args.prune, reload_rspamd=args.reload)
    if args.command == "dns":
        if not args.domain:
            sys.exit("usage: generate_dkim.py dns <domain>")
        return cmd_dns(args.domain)

    # Anything else (including the legacy bare `generate_dkim.py <domain>`)
    if _DOMAIN_RE.match(args.command):
        sys.exit(f"Refusing to mint a key for {args.command} directly.\n\n" + ROTATE_GUIDANCE)
    sys.exit(f"unknown command {args.command!r} -- expected sync | backfill | dns <domain>")


if __name__ == "__main__":
    main()
