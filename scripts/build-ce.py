#!/usr/bin/env python3
"""Generate the Community Edition tree from this (Enterprise) repository.

CE is a *generated artifact*. Nothing in mailyte-mail-server-community is
hand-edited except the paths listed under `ce_local` in ce-manifest.yml. See
plans/02-mailyte-community/phase-06-ce-generation.md for why: hand-porting
failed twice, most recently losing 29 `fix(...)` commits in 13 days.

**Generation works by SUBTRACTION.** Everything in this repo ships in CE unless
the manifest drops it. That is the safety property — a route's supporting
libraries come along automatically and cannot be forgotten. A hand-port of the
mailbox API would have missed shared/imap_mail.py, and because app.py swallows
ImportError it would have shipped a CE whose webmail silently had no endpoints
rather than failing the build.

Usage:
    .venv/bin/python scripts/build-ce.py --out /tmp/ce
    .venv/bin/python scripts/build-ce.py --out /tmp/ce --diff ../mailyte-mail-server-community
    .venv/bin/python scripts/build-ce.py --seed-patches ../mailyte-mail-server-community

Needs PyYAML, which lives in this repo's .venv rather than the system python.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - operator-facing message
    sys.exit(
        "PyYAML not found. Run with this repo's venv:\n"
        "    .venv/bin/python scripts/build-ce.py ..."
    )

REPO = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO / "ce-manifest.yml"
PATCH_DIR = REPO / "scripts" / "ce-patches"

# Compose files that get the service/volume surgery. docker-compose.cloud.yml is
# dropped wholesale by the manifest, so it is deliberately absent here.
COMPOSE_FILES = [
    "docker-compose.yml",
    "docker-compose.dev.yml",
    "docker-compose.prod.yml",
    "docker-compose.override.yml",
]

ROUTES_DIR = "worker/api/routes"
APP_PY = "worker/api/app.py"


# --------------------------------------------------------------------------
# staging
# --------------------------------------------------------------------------


def tracked_files() -> list[str]:
    """Every git-tracked path in this repo.

    git is the source of truth rather than a filesystem walk so that .venv,
    __pycache__, logs/, storage/ and secrets/ can never leak into a published
    CE tree by accident.
    """
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return [line for line in out.stdout.splitlines() if line]


def stage(out: Path) -> int:
    if out.exists():
        shutil.rmtree(out)
    count = 0
    for rel in tracked_files():
        src = REPO / rel
        if not src.is_file():  # submodule or deleted-but-tracked
            continue
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
    return count


# --------------------------------------------------------------------------
# drops
# --------------------------------------------------------------------------


def drop_paths(out: Path, paths: list[str]) -> list[str]:
    removed = []
    for rel in paths:
        target = out / rel.rstrip("/")
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(rel)
        elif target.is_file():
            target.unlink()
            removed.append(rel)
    return removed


def drop_worker_dirs(out: Path, names: list[str]) -> list[str]:
    return drop_paths(out, [f"worker/{n}" for n in names])


def drop_route_files(out: Path, names: list[str]) -> list[str]:
    return drop_paths(out, [f"{ROUTES_DIR}/{n}.py" for n in names])


def strip_package_init(out: Path, names: list[str]) -> int:
    """Remove dropped modules from routes/__init__.py's re-export block.

    Unlike app.py's table, this one is NOT forgiving: `from .rag import router`
    raises ModuleNotFoundError at import time and takes the whole routes package
    with it, so every surviving route dies too. Missing this is what made the
    first generated tree unimportable.
    """
    init = out / ROUTES_DIR / "__init__.py"
    if not init.exists():
        return 0
    dropped = set(names)
    kept, removed = [], 0
    for line in init.read_text().splitlines(keepends=True):
        m = re.match(r"\s*from\s+\.([a-z_][a-z0-9_]*)\s+import\b", line)
        if m and m.group(1) in dropped:
            removed += 1
            continue
        kept.append(line)
    init.write_text("".join(kept))
    return removed


def derive_doc_drops(out: Path, drop: dict, keep: set[str]) -> list[str]:
    """Docs for features CE does not ship, derived from the drop lists.

    Derived rather than hand-listed so it maintains itself: drop a feature and
    its documentation goes with it, with no second list to forget. A public CE
    that documents endpoints returning 404 is a support burden and reads as a
    crippled build rather than a deliberate edition.

    The directory decides which list to match, which is what keeps the stubs
    honest -- `monitoring` survives as a 503 route but its worker is gone, so
    docs/api/monitoring.md stays and docs/worker/monitoring.md goes.
    """
    routes = set(drop.get("route_modules", []))
    workers = set(drop.get("worker_dirs", []))
    everything = routes | workers | set(drop.get("compose_services", []))

    docs = out / "docs"
    if not docs.is_dir():
        return []

    targets = []
    for md in docs.rglob("*.md"):
        rel = md.relative_to(docs)
        stem, parent = md.stem, rel.parent.as_posix()
        if stem in keep:
            continue
        # docs/versions/<v>/<area>/... mirrors the live tree one level down.
        area = parent.split("/")[-1] if parent != "." else ""
        if area == "api":
            match = stem in routes
        elif area == "worker":
            match = stem in workers
        else:
            match = stem in everything
        if match:
            targets.append(f"docs/{rel.as_posix()}")
    return sorted(targets)


def derive_test_drops(out: Path, dropped_workers: list[str]) -> list[tuple[str, str]]:
    """Tests that target a feature CE does not ship.

    Two signals, both precise, neither a filename guess:

    1. The test puts a DROPPED worker's directory on sys.path. These tests say
       so explicitly (`sys.path.insert(0, project_root / "worker" / "migration")`)
       because each worker is its own container with its own top-level modules.
       This is the strongest signal available and needs no resolution guessing.
    2. A first-party import that resolves nowhere on the paths a test could
       plausibly use -- the repo root, worker/api, tests, or any SURVIVING
       worker directory.

    Returns (path, reason) so every removal is auditable rather than silent.
    """
    tests = out / "tests"
    if not tests.is_dir():
        return []

    # Each worker runs as its own container with its own dir as the import root,
    # so `services.config_service` is worker-local, not a repo-wide module.
    worker_roots = [p for p in (out / "worker").iterdir() if p.is_dir()] \
        if (out / "worker").is_dir() else []
    search_roots = [out, out / "worker/api", tests, *worker_roots]

    def resolves(base: Path, dotted: str) -> bool:
        rel = dotted.replace(".", "/")
        return (base / f"{rel}.py").exists() or (base / rel).is_dir()

    drops = []
    for py in sorted(tests.rglob("test_*.py")):
        if "__pycache__" in py.parts:
            continue
        text = py.read_text(errors="ignore")
        rel_path = str(py.relative_to(out))

        lines = text.splitlines()
        path_lines = [ln for ln in lines if "sys.path" in ln]

        def targets(worker: str) -> bool:
            """True only if the worker's dir is actually put on sys.path.

            A mere mention is not enough: test_security_regressions.py lists
            `worker/activesync/Dockerfile` among the images it checks run
            non-root, and test_alertmanager_route.py names the migration tests
            in a comment. Sweeping either away on a substring match would delete
            a security regression test to tidy up a string.
            """
            pat = rf'worker["\']?\s*[/,]\s*["\']?{worker}\b'
            if any(re.search(pat, ln) for ln in path_lines):
                return True
            for ln in lines:
                m = re.match(rf"\s*(\w+)\s*=.*{pat}", ln)
                if not m:
                    continue
                # The path is often built into a variable first, then inserted.
                if any(m.group(1) in p for p in path_lines):
                    return True
                # Or loaded straight off disk without sys.path at all --
                # test_rag_vector_size.py does `RAG_CONFIG_PATH = Path(...) /
                # "worker" / "rag" / "config.py"` and hands it to importlib.
                if "Path(" in ln and "importlib" in text:
                    return True
            return False

        hit = next((w for w in dropped_workers if targets(w)), None)
        if hit:
            drops.append((rel_path, f"targets dropped worker '{hit}'"))
            continue

        for raw in text.splitlines():
            m = re.match(r"\s*(?:from|import)\s+([a-z_][a-z0-9_.]*)", raw)
            if not m or not m.group(1).startswith(FIRST_PARTY):
                continue
            mod = m.group(1)
            if not any(resolves(r, mod) for r in search_roots):
                drops.append((rel_path, f"imports dropped '{mod}'"))
                break
    return drops


def derive_schema_drops(out: Path, route_names: list[str]) -> list[str]:
    """schemas/<route>.py for every dropped route module.

    CE ships the schemas/ package (see the manifest note): because CE is now
    generated from EE, its routes and EE's are the same code, so response_model
    validation is as safe here as there. Only the schemas belonging to dropped
    routes go.
    """
    schemas = out / "worker/api/schemas"
    if not schemas.is_dir():
        return []
    return [
        f"worker/api/schemas/{n}.py"
        for n in route_names
        if (schemas / f"{n}.py").exists()
    ]


def prune_docs_nav(out: Path, removed: list[str]) -> list[str]:
    """Drop mkdocs nav entries pointing at files generation removed.

    Line-based so the config's comments and ordering survive. Returns section
    headers that lost every child -- an empty section renders as a dead menu
    entry, so it is reported rather than silently left behind.
    """
    cfg = out / "docs/mkdocs.yml"
    if not cfg.exists():
        return []

    gone = {r[len("docs/"):] for r in removed}
    lines = cfg.read_text().splitlines(keepends=True)

    kept = []
    for line in lines:
        m = re.match(r"^(\s*)-\s+.*:\s*(\S+\.md)\s*$", line)
        if m and m.group(2) in gone:
            continue
        kept.append(line)
    cfg.write_text("".join(kept))

    # A header is `- Title:` with nothing but deeper-indented entries under it.
    warnings = []
    for i, line in enumerate(kept):
        m = re.match(r"^(\s*)-\s+[^:]+:\s*$", line)
        if not m:
            continue
        indent = len(m.group(1))
        nxt = next(
            (l for l in kept[i + 1:] if l.strip() and not l.strip().startswith("#")), ""
        )
        if not nxt or (len(nxt) - len(nxt.lstrip())) <= indent:
            warnings.append(f"docs/mkdocs.yml: nav section '{line.strip()}' is now empty")
    return warnings


def strip_route_table(out: Path, names: list[str]) -> tuple[int, list[str]]:
    """Remove dropped modules from app.py's `route_modules` table.

    app.py's loop already catches ImportError and continues, so deleting the
    file alone is functionally sufficient. The table entry is stripped anyway so
    a CE operator's startup log is not full of warnings about code that is
    absent by design — an expected warning trains people to ignore warnings.

    Returns (lines_removed, orphaned_comment_warnings).
    """
    app = out / APP_PY
    lines = app.read_text().splitlines(keepends=True)

    start = next(i for i, ln in enumerate(lines) if "route_modules = [" in ln)
    end = next(i for i in range(start, len(lines)) if lines[i].rstrip() == "]")

    dropped = set(names)
    keep: list[str] = []
    removed = 0
    warnings: list[str] = []

    for i, line in enumerate(lines):
        if start < i < end:
            m = re.match(r'\s*\("([a-z_]+)",', line)
            if m and m.group(1) in dropped:
                removed += 1
                continue
            # A comment left behind that names something now gone is worse than
            # no comment: it documents ordering constraints against modules the
            # reader cannot find. Flag rather than guess at its extent.
            if line.lstrip().startswith("#"):
                for name in dropped:
                    # Bare identifier only. `\b` alone produces false positives
                    # against URL paths and hyphenated names -- "/platform/auth/"
                    # and "mailbox-auth" both look like a reference to the
                    # dropped `auth` module without this.
                    if re.search(rf"(?<![\w/-]){name}(?![\w/-])", line):
                        warnings.append(f"{APP_PY}:{i + 1} comment references dropped '{name}'")
                        break
        keep.append(line)

    app.write_text("".join(keep))
    return removed, warnings


# --------------------------------------------------------------------------
# compose surgery
# --------------------------------------------------------------------------


def _section_bounds(lines: list[str]) -> dict[str, tuple[int, int]]:
    """Line ranges of the top-level compose keys (services:, volumes:, ...)."""
    tops = [i for i, ln in enumerate(lines) if re.match(r"^[a-z_]+:\s*$", ln)]
    bounds = {}
    for idx, i in enumerate(tops):
        name = lines[i].split(":")[0]
        end = tops[idx + 1] if idx + 1 < len(tops) else len(lines)
        bounds[name] = (i + 1, end)
    return bounds


def _remove_entries(lines: list[str], lo: int, hi: int, names: set[str]) -> tuple[list[str], set[str]]:
    """Delete 2-space-indented `name:` blocks within [lo, hi).

    Text surgery rather than yaml.safe_load + dump on purpose: this compose file
    is ~2300 lines of heavily commented operator documentation, and a
    round-trip through PyYAML would discard every comment in it. Verified safe
    because the file contains no anchors, aliases or merge keys — if that ever
    changes, this function must change with it.
    """
    entry_starts = [
        i for i in range(lo, hi) if re.match(r"^  [a-z_][a-z0-9_.-]*:", lines[i])
    ]
    cut: set[int] = set()
    hit: set[str] = set()

    for idx, i in enumerate(entry_starts):
        name = lines[i].strip().split(":")[0]
        if name not in names:
            continue
        hit.add(name)
        end = entry_starts[idx + 1] if idx + 1 < len(entry_starts) else hi
        cut.update(range(i, end))
        # Absorb the comment block documenting this entry: contiguous comment
        # lines directly above, stopping at a blank line or another entry.
        j = i - 1
        while j >= lo and lines[j].strip().startswith("#"):
            cut.add(j)
            j -= 1

    return [ln for i, ln in enumerate(lines) if i not in cut], hit


def drop_compose(out: Path, services: list[str], volumes: list[str]) -> tuple[list[str], list[str]]:
    """Strip dropped services and volumes from every compose file."""
    want_svc, want_vol = set(services), set(volumes)
    seen_svc: set[str] = set()
    seen_vol: set[str] = set()
    touched = []

    for fname in COMPOSE_FILES:
        path = out / fname
        if not path.exists():
            continue
        lines = path.read_text().splitlines(keepends=True)
        bounds = _section_bounds(lines)

        # volumes first: removing services shifts every later line index.
        for key, want, seen in (
            ("volumes", want_vol, seen_vol),
            ("services", want_svc, seen_svc),
        ):
            if key not in bounds:
                continue
            lo, hi = _section_bounds(lines)[key]
            lines, hit = _remove_entries(lines, lo, hi, want)
            seen |= hit

        path.write_text("".join(lines))
        touched.append(fname)

    missing = sorted((want_svc - seen_svc)) + sorted((want_vol - seen_vol))
    return touched, missing


# --------------------------------------------------------------------------
# capabilities.py generation
# --------------------------------------------------------------------------


CAPABILITIES_TEMPLATE = '''"""
Capability Manifest API

GET /api/v1/capabilities -- the whole CE/EE gating seam (ADR-001 "How gating
is implemented", console PRD SS11). The Mailyte Console fetches this once at
session start; an absent capability means the nav item is not rendered at
all, and the route redirects.

Unauthenticated by design: the console reads it before anyone has logged in,
so it cannot require a credential -- which is also why it must never return
tenant data, a hostname, or any other deployment detail.

THIS IS THE COMMUNITY EDITION MANIFEST. Unlike mailyte-email-server's copy,
`edition` is a constant, not read from MAILYTE_EDITION. This repo IS the
Community Edition; letting an env var flip it to "enterprise" would let a
misconfigured CE box advertise capabilities its own code does not implement.
Fail safe, not fail open.

!!! DO NOT EDIT !!!
Generated by scripts/build-ce.py from ce-manifest.yml in
mailyte-email-server. Editing this file here is how the capability list and
the route table drifted apart in the first place -- CE advertised webmail,
security and analytics with no code behind any of them. Change the manifest.
"""

from fastapi import APIRouter

router = APIRouter()

APP_VERSION = "{version}"
EDITION = "community"

# Every capability this edition ships. Generated from ce-manifest.yml's
# `capabilities.community`, and cross-checked at build time against
# `capability_owners` so a name here always has code behind it.
COMMUNITY_CAPABILITIES = [
{community}
]

# Documentation only -- never added to the response. ADR-001's gate is on
# scale and commercial capability, never on basic operability: CE gets a
# usable Mailcow-class panel, not a crippled demo.
ENTERPRISE_ONLY_CAPABILITIES = [
{enterprise}
]


@router.get(
    "/",
    summary="Get edition capability manifest",
    description="Returns the platform edition and the list of capabilities it ships. "
    "Unauthenticated by design -- the console and mailyte-web read this before login to "
    "decide which navigation entries and routes to render. Never exposes tenant data.",
)
async def get_capabilities():
    """Return this deployment's capability manifest.

    A constant, not a query: nothing here depends on the database, so the
    manifest still answers correctly while the rest of the stack is starting
    up -- which is exactly when a console is trying to decide what to render.
    """
    return {{
        "edition": EDITION,
        "version": APP_VERSION,
        "capabilities": list(COMMUNITY_CAPABILITIES),
    }}
'''


def gen_capabilities(out: Path, manifest: dict) -> None:
    caps = manifest["capabilities"]
    body = CAPABILITIES_TEMPLATE.format(
        version=manifest.get("app_version", "1.0.0"),
        community="\n".join(f'    "{c}",' for c in caps["community"]),
        enterprise="\n".join(f'    "{c}",' for c in caps["enterprise_only"]),
    )
    (out / ROUTES_DIR / "capabilities.py").write_text(body)


# --------------------------------------------------------------------------
# ce_local + patches
# --------------------------------------------------------------------------


def restore_ce_local(out: Path, ce_repo: Path, entries: list[dict]) -> list[str]:
    """Copy back the paths CE owns and the generator must never author.

    Chief among them alembic/versions/: CE's chain (0003-0011) does not
    correspond to EE's numbering and published instances sit at specific
    revisions, so renumbering is not automatable and must not be attempted.
    """
    restored = []
    for entry in entries:
        rel = entry["path"].rstrip("/")
        src, dst = ce_repo / rel, out / rel
        if not src.exists():
            continue
        if dst.exists():
            shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        restored.append(rel)
    return restored


def patch_targets(manifest: dict) -> list[str]:
    return [p["path"].rstrip("/") for p in manifest.get("patch", []) if "path" in p]


def seed_patches(ce_repo: Path, manifest: dict) -> list[str]:
    """Bootstrap the patch set from CE's current content.

    Each `patch` entry is a file where CE deliberately differs from EE. Rather
    than hand-writing nine diffs, capture today's CE version as the initial
    patch. From then on they behave as patches must: when EE changes a patched
    region the apply FAILS, which is the point — a failed patch says "EE moved,
    re-derive the CE variant" instead of silently keeping a stale override.
    """
    PATCH_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for rel in patch_targets(manifest):
        ee_file, ce_file = REPO / rel, ce_repo / rel
        if not (ee_file.is_file() and ce_file.is_file()):
            continue  # directory-scoped or schema-level entries: hand-written
        diff = subprocess.run(
            ["diff", "-u", str(ee_file), str(ce_file)],
            capture_output=True,
            text=True,
        ).stdout
        if not diff.strip():
            continue
        # Normalise the headers so `git apply` can locate the file in the tree.
        lines = diff.splitlines(keepends=True)
        lines[0] = f"--- a/{rel}\n"
        lines[1] = f"+++ b/{rel}\n"
        dest = PATCH_DIR / (rel.replace("/", "__") + ".patch")
        dest.write_text("".join(lines))
        written.append(str(dest.relative_to(REPO)))
    return written


def apply_patches(out: Path) -> tuple[list[str], list[str]]:
    if not PATCH_DIR.is_dir():
        return [], []
    applied, failed = [], []
    for patch in sorted(PATCH_DIR.glob("*.patch")):
        res = subprocess.run(
            ["git", "apply", "--unsafe-paths", f"--directory={out}", str(patch)],
            cwd=out,
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            applied.append(patch.name)
        else:
            failed.append(f"{patch.name}: {res.stderr.strip().splitlines()[:1]}")
    return applied, failed


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

FIRST_PARTY = (
    "shared.", "utils.", "routes.", "schemas.", "database.", "services.",
    # Tests reach across worker boundaries with the full path
    # (`from worker.dashboard.app import DashboardService`), which neither the
    # sys.path signal nor the bare-module prefixes above would catch.
    "worker.",
)


def verify_imports(out: Path) -> list[str]:
    """Every first-party import in surviving runtime code must resolve.

    This is the check that makes subtraction safe. app.py catches ImportError
    and carries on, so a route whose support file was dropped degrades into a
    silently missing endpoint rather than a failed build — the one way this
    generator could ship a quietly broken CE. Fail the build loudly instead.

    RELATIVE imports are checked too, and that is not a detail: routes/__init__.py
    re-exports every router with `from .rag import router`, which is fatal rather
    than forgiving — it takes the whole routes package down, and with it every
    surviving endpoint. An absolute-only version of this check passed a tree that
    could not be imported at all.
    """
    problems = []
    # Runtime code only. tests/ is covered by actually running the suite, and
    # scanning it here just produces noise from optional third-party imports.
    scan_roots = [out / "worker", out / "shared", out / "database"]

    def resolves(base: Path, dotted: str) -> bool:
        rel = dotted.replace(".", "/")
        return (base / f"{rel}.py").exists() or (base / rel).is_dir()

    def roots_for(py: Path) -> list[Path]:
        """sys.path as the file will actually see it.

        Each worker is its own container with its own directory as the import
        root, so worker/rate_limiter/app.py's `services.cache_service` is
        worker-local. Resolving it only against the repo root reports nine
        false failures for code that is perfectly fine.
        """
        roots = [out / "worker/api", out]
        parts = py.relative_to(out).parts
        if len(parts) > 1 and parts[0] == "worker":
            roots.insert(0, out / "worker" / parts[1])
        return roots

    for root in scan_roots:
        if not root.is_dir():
            continue
        for py in sorted(root.rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            search_roots = roots_for(py)
            for raw in py.read_text(errors="ignore").splitlines():
                rel_m = re.match(r"\s*from\s+(\.+)([a-z_][a-z0-9_.]*)?\s+import\b", raw)
                if rel_m:
                    dots, tail = rel_m.group(1), rel_m.group(2)
                    if not tail:
                        continue  # `from . import x` — package itself, always present
                    base = py.parent
                    for _ in range(len(dots) - 1):  # each extra dot walks up one package
                        base = base.parent
                    if not resolves(base, tail):
                        problems.append(
                            f"{py.relative_to(out)}: unresolved relative import "
                            f"'{dots}{tail}'"
                        )
                    continue

                abs_m = re.match(r"\s*(?:from|import)\s+([a-z_][a-z0-9_.]*)", raw)
                if not abs_m:
                    continue
                mod = abs_m.group(1)
                if not mod.startswith(FIRST_PARTY):
                    continue
                if not any(resolves(r, mod) for r in search_roots):
                    problems.append(f"{py.relative_to(out)}: unresolved import '{mod}'")
    return sorted(set(problems))


def verify_capabilities(out: Path, manifest: dict) -> tuple[list[str], list[str]]:
    """Assert every advertised capability has something behind it.

    The absence of this check is the direct cause of all four phase-0
    contradictions — CE advertised webmail with no mailbox API, so the public
    mailyte-webmail repo could not log into the public CE server.
    """
    owners = manifest.get("capability_owners", {})
    errors, warnings = [], []

    for cap in manifest["capabilities"]["community"]:
        owner = owners.get(cap)
        if owner is None:
            errors.append(f"capability '{cap}' has no entry in capability_owners")
            continue
        kind, _, value = str(owner).partition(":")
        if kind == "route":
            if not (out / ROUTES_DIR / f"{value}.py").exists():
                errors.append(f"capability '{cap}' -> route '{value}' was dropped")
        elif kind == "stub":
            warnings.append(f"capability '{cap}' is advertised but stubbed (503)")
        elif kind == "script":
            if not (out / value).exists():
                errors.append(f"capability '{cap}' -> missing {value}")
        elif kind == "unverified":
            warnings.append(f"capability '{cap}' has no traced owner — resolve or drop it")

    overlap = set(manifest["capabilities"]["community"]) & set(
        manifest["capabilities"]["enterprise_only"]
    )
    for cap in sorted(overlap):
        errors.append(f"capability '{cap}' is in BOTH community and enterprise_only")

    return errors, warnings


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that tolerates Compose's own tags (!override, !reset)."""


_ComposeLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def verify_compose(out: Path, dropped: set[str]) -> list[str]:
    """Every compose file must still parse, and no dropped service may survive.

    The service/volume removal is line surgery on a ~2300-line file, which is
    the riskiest thing this script does -- a botched range silently yields a
    file that Docker refuses at deploy time rather than here.
    """
    problems = []
    for fname in COMPOSE_FILES:
        path = out / fname
        if not path.exists():
            continue
        try:
            doc = yaml.load(path.read_text(), Loader=_ComposeLoader) or {}
        except yaml.YAMLError as exc:
            problems.append(f"{fname}: no longer parses -- {str(exc)[:90]}")
            continue
        svc_map = doc.get("services") or {}
        services = set(svc_map.keys())
        for name in sorted(services & dropped):
            problems.append(f"{fname}: dropped service '{name}' survived")
        if fname == "docker-compose.yml" and "api" not in services:
            problems.append(f"{fname}: core service 'api' is missing")

        declared = set((doc.get("volumes") or {}).keys())
        for svc, conf in svc_map.items():
            for mount in (conf or {}).get("volumes") or []:
                if not isinstance(mount, str):
                    mount = mount.get("source", "")
                source = mount.split(":")[0]
                # Bind mounts are paths; named volumes must be declared.
                if not source or source.startswith((".", "/", "$")):
                    continue
                if source not in declared:
                    problems.append(
                        f"{fname}: service '{svc}' mounts undeclared volume '{source}'"
                    )

        # depends_on pointing at a service that was dropped is equally fatal.
        for svc, conf in svc_map.items():
            for dep in (conf or {}).get("depends_on") or {}:
                if dep not in services:
                    problems.append(
                        f"{fname}: service '{svc}' depends on missing '{dep}'"
                    )
    return problems


def verify_ce_local_coverage(ce_repo: Path, manifest: dict) -> list[str]:
    """Every file CE tracks and EE does not must be claimed by a `ce_local` entry.

    Generation is destructive: a CE-only file nothing claims is silently deleted
    on the next publish. This check found docs/operations/backups.md and
    docs/worker/cloud-sync.md on the first run — PARITY-AUDIT.md had counted
    "6 CE-only docs" without listing them, and only 4 were the api/examples set.

    Run against the CE repo rather than the generated tree, so a new CE-only
    file added by hand between releases is caught at the next build.
    """
    def tracked(repo: Path) -> set[str]:
        res = subprocess.run(
            ["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True
        )
        return set(res.stdout.split())

    claims = [e["path"].rstrip("/") for e in manifest.get("ce_local", [])]
    problems = []
    for rel in sorted(tracked(ce_repo) - tracked(REPO)):
        if not any(rel == c or rel.startswith(c + "/") for c in claims):
            problems.append(f"CE-only file unclaimed by ce_local: {rel}")
    return problems


def publish(out: Path, ce_repo: Path, write: bool) -> tuple[list[str], list[str], list[str]]:
    """Sync the generated tree into the CE repo's working tree.

    Deliberately stops at the working tree: nothing is committed and nothing is
    pushed. CE is public, so the irreversible step stays a human one -- review
    `git diff` there, then push.

    Only git-TRACKED files are removed. A CE checkout also holds .env, logs/,
    storage/ and secrets/ that are not ours to delete, and a publish that wipes
    an operator's local state is a far worse failure than a stale file.
    """
    generated = {
        p.relative_to(out).as_posix()
        for p in out.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    tracked = set(
        subprocess.run(
            ["git", "ls-files"], cwd=ce_repo, capture_output=True, text=True, check=True
        ).stdout.split()
    )

    added = sorted(generated - tracked)
    removed = sorted(tracked - generated)
    changed = sorted(
        rel for rel in (generated & tracked)
        if (out / rel).read_bytes() != (ce_repo / rel).read_bytes()
    )

    if write:
        for rel in added + changed:
            dst = ce_repo / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out / rel, dst)
        for rel in removed:
            (ce_repo / rel).unlink(missing_ok=True)

    return added, changed, removed


def diff_against(out: Path, ce_repo: Path) -> str:
    res = subprocess.run(
        ["diff", "-rq", "--exclude=.git", "--exclude=__pycache__", str(out), str(ce_repo)],
        capture_output=True,
        text=True,
    )
    return res.stdout


# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, help="where to write the generated CE tree")
    ap.add_argument("--diff", type=Path, help="CE repo to diff the result against")
    ap.add_argument("--seed-patches", type=Path, metavar="CE_REPO",
                    help="bootstrap scripts/ce-patches/ from a CE repo, then exit")
    ap.add_argument("--ce-local-from", type=Path,
                    help="CE repo to restore ce_local paths from (defaults to --diff)")
    ap.add_argument("--publish", type=Path, metavar="CE_REPO",
                    help="sync the generated tree into CE_REPO's working tree "
                         "(shows the plan only; add --write to apply). Never commits "
                         "or pushes -- CE is public, so that stays a human step.")
    ap.add_argument("--write", action="store_true",
                    help="with --publish, actually write the files")
    args = ap.parse_args()

    manifest = yaml.safe_load(MANIFEST_PATH.read_text())

    if args.seed_patches:
        written = seed_patches(args.seed_patches.resolve(), manifest)
        print(f"seeded {len(written)} patches:")
        for w in written:
            print(f"  {w}")
        return 0

    if not args.out:
        ap.error("--out is required (or use --seed-patches)")

    out = args.out.resolve()
    drop = manifest["drop"]

    print(f"staging {stage(out)} tracked files -> {out}")

    drop_paths(out, drop.get("paths", []))
    drop_worker_dirs(out, drop.get("worker_dirs", []))
    drop_route_files(out, drop.get("route_modules", []))
    n_entries, table_warnings = strip_route_table(out, drop.get("route_modules", []))
    n_init = strip_package_init(out, drop.get("route_modules", []))
    print(f"dropped {len(drop.get('route_modules', []))} route modules "
          f"({n_entries} table entries, {n_init} package re-exports), "
          f"{len(drop.get('worker_dirs', []))} worker dirs")

    schema_drops = derive_schema_drops(out, drop.get("route_modules", []))
    drop_paths(out, schema_drops)

    doc_drops = derive_doc_drops(out, drop, set(drop.get("docs_keep", [])))
    drop_paths(out, doc_drops)
    nav_warnings = prune_docs_nav(out, doc_drops)

    test_drops = derive_test_drops(out, drop.get("worker_dirs", []))
    drop_paths(out, [p for p, _ in test_drops])
    print(f"derived drops: {len(doc_drops)} docs, {len(schema_drops)} schemas, "
          f"{len(test_drops)} tests")
    for path, reason in test_drops:
        print(f"    - {path}  ({reason})")

    touched, missing = drop_compose(out, drop.get("compose_services", []),
                                    drop.get("compose_volumes", []))
    print(f"compose surgery on {len(touched)} files")

    gen_capabilities(out, manifest)

    ce_repo = (args.ce_local_from or args.diff or args.publish)
    if ce_repo:
        restored = restore_ce_local(out, ce_repo.resolve(), manifest.get("ce_local", []))
        print(f"restored {len(restored)} ce_local paths")

    applied, failed = apply_patches(out)
    print(f"patches: {len(applied)} applied, {len(failed)} failed")

    # ---- verification -------------------------------------------------
    import_problems = verify_imports(out)
    cap_errors, cap_warnings = verify_capabilities(out, manifest)
    local_problems = verify_ce_local_coverage(ce_repo.resolve(), manifest) if ce_repo else []
    compose_problems = verify_compose(out, set(drop.get("compose_services", [])))

    print("\n--- verification ---")
    for label, items in (
        ("MISSING from compose (manifest names it, file doesn't have it)", missing),
        ("UNRESOLVED IMPORTS", import_problems),
        ("CAPABILITY ERRORS", cap_errors),
        ("UNCLAIMED CE-ONLY FILES (generation would delete these)", local_problems),
        ("COMPOSE PROBLEMS", compose_problems),
        ("PATCH FAILURES", failed),
    ):
        if items:
            print(f"\n{label}: {len(items)}")
            for i in items[:25]:
                print(f"  ! {i}")

    for label, items in (
        ("capability warnings", cap_warnings),
        ("orphaned comments in app.py", table_warnings),
        ("docs nav", nav_warnings),
    ):
        if items:
            print(f"\n{label}: {len(items)}")
            for i in items[:25]:
                print(f"  ~ {i}")

    if args.diff:
        report = diff_against(out, args.diff.resolve())
        only_gen = [l for l in report.splitlines() if l.startswith(f"Only in {out}")]
        only_ce = [l for l in report.splitlines() if "mailyte-mail-server-community" in l
                   and l.startswith("Only in")]
        differ = [l for l in report.splitlines() if l.startswith("Files ")]
        print(f"\n--- diff vs {args.diff.name} ---")
        print(f"  only in generated : {len(only_gen)}")
        print(f"  only in CE repo   : {len(only_ce)}")
        print(f"  differing         : {len(differ)}")

    blocking = (
        import_problems + cap_errors + failed + missing + local_problems + compose_problems
    )
    if blocking:
        print(f"\nFAILED: {len(blocking)} blocking problem(s)")
        return 1

    if args.publish:
        # Refused above if anything was blocking -- publishing a tree that
        # failed its own gates is the exact failure this whole mechanism exists
        # to prevent.
        added, changed, removed = publish(out, args.publish.resolve(), args.write)
        verb = "published" if args.write else "would publish"
        print(f"\n--- {verb} to {args.publish} ---")
        print(f"  added   : {len(added)}")
        print(f"  changed : {len(changed)}")
        print(f"  removed : {len(removed)}")
        for rel in removed[:20]:
            print(f"    - {rel}")
        if len(removed) > 20:
            print(f"    ... and {len(removed) - 20} more")
        if args.write:
            print("\nNothing was committed or pushed. Review `git diff` in the CE "
                  "repo, then push when you are satisfied.")
        else:
            print("\nDry run. Re-run with --write to apply.")

    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
