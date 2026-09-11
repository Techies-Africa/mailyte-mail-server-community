"""A folded header must never carry its CRLF into JSON or into a new message.

The compat32 parser returns folded header values verbatim. One real subject
("[NC-IZF-2821] ... Dedicated IP for\r\n mailyte.com VPS Quasar") travelled
parse -> webmail reply -> send, where EmailMessage's policy refused the CRLF
("Header values may not contain linefeed or carriage return characters") and
the send failed. Parse now unfolds every decoded header; build_message
unfolds every caller-supplied value as defence in depth -- which is also the
guard that keeps a smuggled CRLF from ever becoming a second header.
"""

from shared.imap_mail import _decode_header_value, build_message

FOLDED_SUBJECT = (
    "Re: [NC-IZF-2821] Namecheap Chat Follow-up: Dedicated IP for\r\n"
    " mailyte.com VPS Quasar"
)
UNFOLDED_SUBJECT = (
    "Re: [NC-IZF-2821] Namecheap Chat Follow-up: Dedicated IP for"
    " mailyte.com VPS Quasar"
)


class TestDecodeHeaderValue:
    def test_folded_subject_unfolds_to_one_line(self):
        assert _decode_header_value(FOLDED_SUBJECT) == UNFOLDED_SUBJECT

    def test_tab_folding_collapses_to_a_space(self):
        assert _decode_header_value("a\r\n\tb") == "a b"

    def test_rfc2047_still_decodes(self):
        assert _decode_header_value("=?UTF-8?B?w6l0w6k=?=") == "été"


class TestBuildMessageHeaderSafety:
    def test_folded_subject_is_stored_and_serialises(self):
        # Before the fix this raised ValueError at header-store time.
        message = build_message(
            from_address="peace@techies.africa",
            to=["billing@namecheap.com"],
            subject=FOLDED_SUBJECT,
            body_html="<p>hi</p>",
        )
        assert str(message["Subject"]) == UNFOLDED_SUBJECT
        message.as_bytes()

    def test_crlf_in_name_and_recipient_cannot_inject_a_header(self):
        message = build_message(
            from_address="peace@techies.africa",
            from_name="Kanu\r\nPeace",
            to=["evil@example.com\r\nBcc: hidden@example.com"],
            subject="ok",
            body_text="hi",
        )
        # The smuggled text may survive as CONTENT of the To header (folded
        # continuation lines start with whitespace); it must never start a
        # line of its own, which is what would make it a real header.
        lines = message.as_bytes().splitlines()
        assert not any(line.startswith(b"Bcc:") for line in lines)
        assert "Kanu Peace" in str(message["From"])

    def test_threading_headers_unfold(self):
        message = build_message(
            from_address="peace@techies.africa",
            subject="s",
            body_text="t",
            in_reply_to="<x@namecheap.com>",
            references="<a@example.com>\r\n <b@example.com>",
        )
        assert str(message["In-Reply-To"]) == "<x@namecheap.com>"
        assert str(message["References"]) == "<a@example.com> <b@example.com>"

    def test_attachment_filename_with_newline_serialises(self):
        message = build_message(
            from_address="peace@techies.africa",
            subject="s",
            body_text="t",
            attachments=[
                {"name": "inv\r\noice.pdf", "type": "application/pdf", "content": b"%PDF"}
            ],
        )
        message.as_bytes()
