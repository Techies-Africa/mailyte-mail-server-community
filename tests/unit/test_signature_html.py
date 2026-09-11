#!/usr/bin/env python3
"""
Unit tests for worker/api/utils/signature_html.py -- the outbound signature
allowlist, and in particular the one shape of data: URI it lets through.
"""

import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "worker" / "api"))

from utils.signature_html import MAX_SIGNATURE_BYTES, sanitize_signature  # noqa: E402

PNG_DATA_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class TestInlineImages:
    def test_data_image_src_survives(self):
        """The reported bug: an inserted picture was stripped to a bare <img alt>."""
        out = sanitize_signature(f'<p>Jane<img src="{PNG_DATA_URI}" alt="card"></p>')
        assert f'src="{PNG_DATA_URI}"' in out
        assert 'alt="card"' in out

    @pytest.mark.parametrize("subtype", ["png", "jpeg", "jpg", "gif", "webp"])
    def test_every_raster_type_allowed(self, subtype):
        uri = f"data:image/{subtype};base64,AAAA"
        assert f'src="{uri}"' in sanitize_signature(f'<img src="{uri}">')

    def test_data_uri_case_insensitive(self):
        uri = "DATA:IMAGE/PNG;BASE64,AAAA"
        assert f'src="{uri}"' in sanitize_signature(f'<img src="{uri}">')

    def test_svg_data_uri_dropped(self):
        """SVG can carry <script>; it is not a raster image and stays out."""
        out = sanitize_signature('<img src="data:image/svg+xml;base64,AAAA">')
        assert "src=" not in out

    def test_data_text_html_dropped(self):
        out = sanitize_signature('<img src="data:text/html;base64,AAAA">')
        assert "src=" not in out

    def test_data_image_on_href_dropped(self):
        """The same URI as a link target is a navigation, not a picture."""
        out = sanitize_signature(f'<a href="{PNG_DATA_URI}">x</a>')
        assert "href=" not in out
        assert ">x</a>" in out

    def test_base64_body_must_be_base64(self):
        out = sanitize_signature('<img src="data:image/png;base64,<script>">')
        assert "src=" not in out

    def test_http_src_still_allowed(self):
        out = sanitize_signature('<img src="https://example.com/logo.png">')
        assert 'src="https://example.com/logo.png"' in out

    def test_javascript_src_still_dropped(self):
        out = sanitize_signature('<img src="javascript:alert(1)">')
        assert "src=" not in out


class TestLimits:
    def test_ceiling_holds_a_real_banner(self):
        """A 300 KB base64 banner -- the case TEXT's 64 KB could not store."""
        big = "A" * (300 * 1024)
        html = f'<img src="data:image/png;base64,{big}">'
        assert len(html) > 64 * 1024
        assert sanitize_signature(html) == f'<img src="data:image/png;base64,{big}"/>'

    def test_over_ceiling_refused(self):
        big = "A" * (MAX_SIGNATURE_BYTES + 1)
        with pytest.raises(ValueError):
            sanitize_signature(f'<img src="data:image/png;base64,{big}">')
