#!/usr/bin/env python3
"""
Import-safety regression tests for the SMTP-credential route modules.

worker/api/app.py imports every route module inside try/except and registers
only the survivors. Both SMTP-credential modules used to build their pooled
SQLAlchemy engine at import time from DB_* env vars, so importing them with
incomplete env (CI's openapi generation sets only DB_PASSWORD; a misconfigured
deploy could do the same) raised during import and the app came up WITHOUT any
/smtp-credentials route -- silently. These tests pin the fix: importing with
no DB_* env must succeed and expose a populated router, while actually asking
for a DB session with broken config must still fail loudly.
"""

import importlib
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "worker" / "api"))

DB_ENV_VARS = ("DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_NAME")

MODULES = ("routes.smtp_credentials", "routes.smtp_credential_reports")


def _fresh_import(module_name):
    """Import the module from scratch under the CURRENT environment."""
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


@pytest.fixture
def no_db_env():
    """Environment with every DB_* var absent (CI openapi-generation shape)."""
    with mock.patch.dict(os.environ):
        for var in DB_ENV_VARS:
            os.environ.pop(var, None)
        yield


@pytest.mark.parametrize("module_name", MODULES)
def test_import_succeeds_without_db_env(no_db_env, module_name):
    """Importing with no DB_* env must not raise -- routes must register."""
    module = _fresh_import(module_name)
    assert module.router is not None
    assert len(module.router.routes) > 0, (
        f"{module_name} imported but exposes no routes -- app.py would "
        "register an empty router and the API paths would be gone"
    )


@pytest.mark.parametrize("module_name", MODULES)
def test_engine_is_not_created_at_import(no_db_env, module_name):
    """The pooled engine must stay unbuilt until the first session request."""
    module = _fresh_import(module_name)
    assert module._engine is None
    assert module._Session is None


@pytest.mark.parametrize("module_name", MODULES)
def test_get_db_session_fails_loudly_on_broken_config(no_db_env, module_name):
    """With DB_* unset the FIRST USE must raise (URL port is the literal
    'None'), not silently hand back a session against a nonsense URL."""
    module = _fresh_import(module_name)
    with pytest.raises(ValueError):
        module.get_db_session()
    # A failed first attempt must not cache a broken half-built state that
    # would make a later, correctly-configured call impossible.
    assert module._Session is None


@pytest.mark.parametrize("module_name", MODULES)
def test_engine_created_once_and_reused(module_name):
    """With valid-looking config the engine is built once and then reused --
    the single-pool behavior the module comment promises."""
    env = {
        "DB_USER": "u",
        "DB_PASSWORD": "p",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "3306",
        "DB_NAME": "db",
    }
    with mock.patch.dict(os.environ, env):
        module = _fresh_import(module_name)
        s1 = module.get_db_session()
        engine_after_first = module._engine
        s2 = module.get_db_session()
        try:
            assert engine_after_first is not None
            assert module._engine is engine_after_first
            assert s1 is not s2  # sessions are per-call, the pool is shared
        finally:
            s1.close()
            s2.close()
    # Leave no cached engine behind for other tests/importers of the module.
    sys.modules.pop(module_name, None)
