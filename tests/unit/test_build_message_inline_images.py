#!/usr/bin/env python3
"""
Unit tests for shared/imap_mail.build_message's inline-image handling: data:
images become Content-ID parts on send, and stay data: URIs in drafts.
"""

import base64
import sys
from email.parser import BytesParser
from email.policy import default as default_policy
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from shared.imap_mail import build_message, embed_data_images, message_bytes  # noqa: E402

# A real 1x1 PNG, so the decoded bytes are a genuine image rather than "AAAA".
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
PNG_URI = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode()
JPEG_URI = "data:image/jpg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0jpegbytes").decode()

BODY = f'<p>Hello</p><p><img src="{PNG_URI}" alt="card" width="200"></p>'


def _roundtrip(message):
    """Serialise and re-parse, so the assertions see what a recipient sees."""
    return BytesParser(policy=default_policy).parsebytes(message_bytes(message))


class TestEmbedDataImages:
    def test_rewrites_src_to_cid_and_returns_part(self):
        html, parts = embed_data_images(BODY)
        assert len(parts) == 1
        part = parts[0]
        assert part["content"] == PNG_BYTES
        assert part["subtype"] == "png"
        assert part["name"] == "image1.png"
        assert f'src="cid:{part["cid"]}"' in html
        assert "data:" not in html
        # Other attributes on the tag are untouched.
        assert 'alt="card" width="200"' in html

    def test_identical_images_share_one_part(self):
        html, parts = embed_data_images(f'<img src="{PNG_URI}"><img src="{PNG_URI}">')
        assert len(parts) == 1
        assert html.count(f"cid:{parts[0]['cid']}") == 2

    def test_jpg_normalised_to_jpeg(self):
        _, parts = embed_data_images(f'<img src="{JPEG_URI}">')
        assert parts[0]["subtype"] == "jpeg"
        assert parts[0]["name"] == "image1.jpg"

    def test_single_quotes_and_case(self):
        html, parts = embed_data_images(f"<IMG SRC='{PNG_URI}'>")
        assert len(parts) == 1
        assert f"SRC='cid:{parts[0]['cid']}'" in html

    def test_malformed_base64_left_alone(self):
        bad = '<img src="data:image/png;base64,!!!notbase64!!!">'
        html, parts = embed_data_images(bad)
        assert parts == []
        assert html == bad

    def test_remote_and_cid_images_untouched(self):
        src = '<img src="https://example.com/a.png"><img src="cid:existing@x">'
        html, parts = embed_data_images(src)
        assert parts == []
        assert html == src


class TestBuildMessage:
    def test_send_produces_related_part_with_cid(self):
        message = build_message(
            from_address="jane@example.com",
            to=["bob@example.com"],
            subject="Hi",
            body_html=BODY,
            inline_images=True,
        )
        parsed = _roundtrip(message)

        # mixed is not needed with no file attachments: alternative(text, related(html, image)).
        assert parsed.get_content_type() == "multipart/alternative"
        text_part, related = parsed.get_payload()
        assert text_part.get_content_type() == "text/plain"
        assert related.get_content_type() == "multipart/related"

        html_part, image_part = related.get_payload()
        assert html_part.get_content_type() == "text/html"
        assert image_part.get_content_type() == "image/png"
        assert image_part.get_content_disposition() == "inline"
        assert image_part.get_payload(decode=True) == PNG_BYTES

        cid = image_part["Content-ID"].strip("<>")
        assert f'src="cid:{cid}"' in html_part.get_content()
        assert "data:" not in html_part.get_content()

    def test_send_with_file_attachment_keeps_structure(self):
        message = build_message(
            from_address="jane@example.com",
            to=["bob@example.com"],
            body_html=BODY,
            attachments=[{"name": "notes.txt", "type": "text/plain", "content": b"hi"}],
            inline_images=True,
        )
        parsed = _roundtrip(message)
        assert parsed.get_content_type() == "multipart/mixed"
        alternative, attachment = parsed.get_payload()
        assert alternative.get_content_type() == "multipart/alternative"
        assert alternative.get_payload()[1].get_content_type() == "multipart/related"
        assert attachment.get_content_disposition() == "attachment"
        assert attachment.get_filename() == "notes.txt"

    def test_draft_keeps_data_uri(self):
        """Drafts are resumed into the editor, which can re-open data: but not cid:."""
        message = build_message(
            from_address="jane@example.com",
            body_html=BODY,
        )
        parsed = _roundtrip(message)
        assert parsed.get_content_type() == "multipart/alternative"
        html_part = parsed.get_payload()[1]
        assert html_part.get_content_type() == "text/html"
        assert PNG_URI in html_part.get_content()

    def test_no_images_means_no_related(self):
        message = build_message(
            from_address="jane@example.com",
            body_html="<p>plain</p>",
            inline_images=True,
        )
        parsed = _roundtrip(message)
        assert parsed.get_payload()[1].get_content_type() == "text/html"

    def test_bcc_strip_still_works_after_embedding(self):
        """The send route deletes Bcc from the Sent copy; embedding must not break that."""
        message = build_message(
            from_address="jane@example.com",
            to=["bob@example.com"],
            bcc=["secret@example.com"],
            body_html=BODY,
            inline_images=True,
        )
        del message["Bcc"]
        assert "Bcc" not in _roundtrip(message)
