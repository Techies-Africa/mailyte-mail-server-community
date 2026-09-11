"""
Outbound signature sanitising.

Part of 04-mailyte-web/02-PRD-webmail-standalone Phase 4, replacing Laravel's
SignatureSanitizer.

A signature is HTML the mailbox holder supplies and this server then attaches
to outgoing mail. Two things follow:

1. It is stored and later re-rendered in the composer, so anything script-like
   that survives is stored XSS against the holder's own session.
2. It goes out over SMTP to other people, so this server would be the thing
   sending them an attack.

DOMPurify guards what the webmail *displays*; nothing guarded what it
*stores and sends*. This does, on the way in, with an allowlist -- a denylist
of "dangerous" tags is the wrong shape, because the set of dangerous things
grows and an allowlist that misses something merely loses formatting.

html.parser from the standard library rather than a dependency: the input is
small, the allowlist is short, and adding a parser to this image for one
feature is not worth it.
"""

import re
from html.parser import HTMLParser

# Formatting a signature legitimately needs. Anything that can execute, load a
# remote resource, or position itself over the page is absent by design --
# notably script, style, iframe, object, embed, form, meta and link.
ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "div",
    "em",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "s",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}

# Per-tag attribute allowlist. `style` is deliberately absent everywhere:
# it carries url(), expression() and position:fixed, and a signature does not
# need it badly enough to justify parsing CSS safely.
ALLOWED_ATTRS = {
    "a": {"href", "title", "target", "rel"},
    "img": {"src", "alt", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}

VOID_TAGS = {"br", "img"}

# Tags whose CONTENT is discarded along with the tag, rather than being kept
# as text. Everywhere else "drop the tag, keep the text" is right -- removing
# a <div>'s content would silently delete what someone wrote. For these it is
# wrong: the body of a <script> is code, not prose, and surfacing it as
# visible text puts `alert(1)` in the signature of every message sent.
RAW_TEXT_TAGS = {"script", "style", "title", "textarea", "noscript", "template"}

# Only schemes that cannot execute. `javascript:` is the obvious one.
SAFE_URL_SCHEMES = ("http://", "https://", "mailto:", "cid:")

# The ONE shape of data: URI that is allowed, and only on <img src>: a base64
# raster image. This is how the webmail's editor embeds a picture the holder
# inserts or pastes -- a logo, a banner card -- and a signature with an image
# in it is the whole reason most people set one. The Laravel sanitiser this
# replaced allowed exactly the same shape.
#
# The check is on the media type, not the scheme: data:text/html is a script
# vector and stays out, as does SVG (it can carry <script>). Never applied to
# href -- a link to a data: URL navigates to it, which is the attack.
#
# The base64 body is matched too rather than only the prefix, so a value that
# starts as an image and turns into something else past the comma is refused.
DATA_IMAGE_RE = re.compile(
    r"^data:image/(?:png|jpe?g|gif|webp);base64,[a-z0-9+/=\s]+$", re.IGNORECASE
)

# A signature is stored in mailbox_preferences.signature_html, MEDIUMTEXT since
# 0020 -- TEXT's 64 KB could not hold even a modest inline banner once
# base64-encoded (a 600px-wide PNG card is 100-300 KB). The webmail scales an
# inserted image down and refuses anything over 1 MB before it is ever sent,
# so this ceiling is a backstop against a hand-written client, not the limit
# a person will meet.
MAX_SIGNATURE_BYTES = 2 * 1024 * 1024


def _safe_url(value: str | None, *, allow_data_image: bool = False) -> bool:
    if not value:
        return False
    candidate = value.strip().lower()
    # A relative URL has no scheme and cannot execute.
    if candidate.startswith(("/", "#", "./", "../")):
        return True
    if candidate.startswith(SAFE_URL_SCHEMES):
        return True
    return allow_data_image and DATA_IMAGE_RE.match(value.strip()) is not None


class _SignatureCleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._open: list[str] = []
        # Depth of nested discard-content tags currently open.
        self._suppress = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in RAW_TEXT_TAGS:
            self._suppress += 1
            return
        if self._suppress:
            return
        if tag not in ALLOWED_TAGS:
            # Drop the tag, keep its text. Removing a <div>'s content along
            # with the div would silently delete the signature someone wrote.
            return
        allowed = ALLOWED_ATTRS.get(tag, set())
        rendered = []
        for name, value in attrs:
            if name not in allowed:
                continue
            if name == "href" and not _safe_url(value):
                continue
            # An inline data: image is fine in src; the same URI in href is a
            # navigation and is not.
            if name == "src" and not _safe_url(value, allow_data_image=(tag == "img")):
                continue
            rendered.append(f' {name}="{(value or "").replace(chr(34), "&quot;")}"')

        if tag == "a":
            # Any link that opens a new tab gets noopener: without it the
            # opened page can reach back through window.opener.
            rendered.append(' rel="noopener noreferrer"')

        if tag in VOID_TAGS:
            self.parts.append(f"<{tag}{''.join(rendered)}/>")
        else:
            self._open.append(tag)
            self.parts.append(f"<{tag}{''.join(rendered)}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in RAW_TEXT_TAGS:
            self._suppress = max(0, self._suppress - 1)
            return
        if self._suppress:
            return
        if tag in VOID_TAGS or tag not in ALLOWED_TAGS:
            return
        if tag in self._open:
            # Close everything opened after this tag too, so malformed input
            # cannot leave the document unbalanced.
            while self._open:
                open_tag = self._open.pop()
                self.parts.append(f"</{open_tag}>")
                if open_tag == tag:
                    break

    def handle_data(self, data: str) -> None:
        if self._suppress:
            return
        self.parts.append(data.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def result(self) -> str:
        while self._open:
            self.parts.append(f"</{self._open.pop()}>")
        return "".join(self.parts)


def sanitize_signature(html: str | None) -> str:
    """Return the signature reduced to safe, allowlisted markup."""
    if not html:
        return ""
    if len(html.encode("utf-8", errors="ignore")) > MAX_SIGNATURE_BYTES:
        raise ValueError("Signature is too large")
    cleaner = _SignatureCleaner()
    cleaner.feed(html)
    cleaner.close()
    return cleaner.result()
