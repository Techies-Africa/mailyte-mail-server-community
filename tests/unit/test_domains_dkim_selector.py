#!/usr/bin/env python3
"""
Unit tests for the DKIM selector fixes in worker/api/routes/domains.py.

generate_dns_records() used to hardcode `default._domainkey.{domain}` while
DNS verification reads domains.dkim_selector -- so for any domain on a
non-default selector (i.e. anything that went through the two-step
rotate/activate flow) the customer was told to publish the key at a name
rspamd never signs with. These tests pin the fixed contract: the DKIM record
name follows the selector the caller passes, and dkim_selector input is
validated as a single DNS label (it becomes both a DNS label and a filename
component in rspamd's shared key directory).
"""

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "worker" / "api"))

import routes.domains as domains  # noqa: E402


def _dkim_records(records):
    return [r for r in records if r["name"].endswith("._domainkey.example.com")]


def test_dns_records_use_the_domains_actual_selector():
    records = domains.generate_dns_records(
        "example.com", "mx.mailyte.com", "PUBKEY", dkim_selector="mailyte20260830120000"
    )
    dkim = _dkim_records(records)
    assert len(dkim) == 1
    assert dkim[0]["name"] == "mailyte20260830120000._domainkey.example.com"
    assert dkim[0]["value"] == "v=DKIM1; k=rsa; p=PUBKEY"


def test_dns_records_default_selector_when_omitted():
    records = domains.generate_dns_records("example.com", "mx.mailyte.com", "PUBKEY")
    assert _dkim_records(records)[0]["name"] == "default._domainkey.example.com"


def test_dns_records_none_selector_falls_back_to_default():
    records = domains.generate_dns_records(
        "example.com", "mx.mailyte.com", "PUBKEY", dkim_selector=None
    )
    assert _dkim_records(records)[0]["name"] == "default._domainkey.example.com"


def test_dns_records_without_public_key_have_no_dkim_record():
    records = domains.generate_dns_records(
        "example.com", "mx.mailyte.com", None, dkim_selector="whatever"
    )
    assert _dkim_records(records) == []


@pytest.mark.parametrize("selector", ["default", "mailyte20260830120000", "s_1", "a"])
def test_validate_domain_data_accepts_label_selectors(selector):
    errors = domains.validate_domain_data({"dkim_selector": selector}, is_update=True)
    assert errors == []


@pytest.mark.parametrize(
    "selector",
    ["sel.ector", "../etc", "a/b", "-lead", "trail-", "a" * 64, ""],
)
def test_validate_domain_data_rejects_non_label_selectors(selector):
    errors = domains.validate_domain_data({"dkim_selector": selector}, is_update=True)
    assert any("selector" in e.lower() for e in errors)


def test_validate_domain_data_allows_absent_or_null_selector():
    assert domains.validate_domain_data({}, is_update=True) == []
    assert domains.validate_domain_data({"dkim_selector": None}, is_update=True) == []


def test_public_key_b64_alias_still_collapses_pem():
    pem = "-----BEGIN PUBLIC KEY-----\nAAAA\nBBBB\n-----END PUBLIC KEY-----\n"
    assert domains._dkim_public_key_b64(pem) == "AAAABBBB"
    assert domains._dkim_public_key_b64("AAAABBBB") == "AAAABBBB"
    assert domains._dkim_public_key_b64(None) is None
