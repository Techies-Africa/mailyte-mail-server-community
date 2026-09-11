#!/usr/bin/env python3
"""
Structural checks on alembic/versions/.

These are cheap, and each one stands for a deploy that has already been
broken by its absence:

* REVISION ID LENGTH. alembic_version.version_num is varchar(32), and
  alembic stamps the new version *after* upgrade() has run. On MySQL, where
  DDL auto-commits, an over-long id therefore applies the schema change and
  then dies with "1406 Data too long for column 'version_num'" -- the deploy
  reports a failed migration, but the schema moved and alembic_version did
  not, so the two disagree afterwards. 0028 was first written with a
  34-character id and did exactly this in production.
* SINGLE HEAD. Two heads make `alembic upgrade head` ambiguous and it
  refuses to run at all.
* NO DANGLING down_revision. A revision pointing at an id that no file
  defines breaks the chain for every revision behind it.

Read straight off the files rather than through alembic's own API so this
needs no database and no alembic.ini resolution.
"""

import re
from pathlib import Path

import pytest

VERSIONS_DIR = Path(__file__).parent.parent.parent / "alembic" / "versions"

# alembic_version.version_num is varchar(32) -- see the module docstring.
MAX_REVISION_ID = 32

REVISION_RE = re.compile(r"^revision[^=]*=\s*[\"']([^\"']+)", re.M)
DOWN_REVISION_RE = re.compile(r"^down_revision[^=]*=\s*(?:[\"']([^\"']+)[\"']|None)", re.M)


def _revisions():
    """{revision_id: (path, down_revision_or_None)} for every migration file."""
    found = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        source = path.read_text()
        revision = REVISION_RE.search(source)
        if not revision:
            continue
        down = DOWN_REVISION_RE.search(source)
        found[revision.group(1)] = (path, down.group(1) if down and down.group(1) else None)
    return found


REVISIONS = _revisions()


def test_versions_directory_is_populated():
    """A path or glob mistake would make every other test here vacuously pass."""
    assert len(REVISIONS) > 20


@pytest.mark.parametrize("revision", sorted(REVISIONS))
def test_revision_id_fits_the_version_num_column(revision):
    assert len(revision) <= MAX_REVISION_ID, (
        f"{revision!r} is {len(revision)} chars; alembic_version.version_num holds "
        f"{MAX_REVISION_ID}. On MySQL this applies the schema change and THEN fails "
        f"stamping it (1406), leaving the database ahead of alembic_version."
    )


@pytest.mark.parametrize("revision", sorted(REVISIONS))
def test_revision_id_matches_its_filename(revision):
    path, _ = REVISIONS[revision]
    assert path.stem == revision


def test_no_dangling_down_revisions():
    dangling = {
        revision: down
        for revision, (_, down) in REVISIONS.items()
        if down is not None and down not in REVISIONS
    }
    assert dangling == {}


def test_chain_has_exactly_one_head():
    parents = {down for _, down in REVISIONS.values() if down is not None}
    heads = sorted(set(REVISIONS) - parents)
    assert len(heads) == 1, f"`alembic upgrade head` is ambiguous with heads: {heads}"


def test_chain_has_exactly_one_base():
    bases = sorted(r for r, (_, down) in REVISIONS.items() if down is None)
    assert len(bases) == 1, f"more than one starting point: {bases}"
