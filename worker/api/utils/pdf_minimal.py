"""
A dependency-free PDF writer for text documents.

Written for the AI consent record (utils/ai_consent.py), which has to hand a
mailbox holder a durable copy of exactly what they agreed to. reportlab is
not in the api image and a PDF library is a heavy dependency for one text
document, so this emits the smallest valid PDF that carries text: PDF 1.4,
the three standard Type 1 fonts every reader ships (Helvetica,
Helvetica-Bold, Courier), one uncompressed content stream per page, and a
correct cross-reference table.

Deliberately uncompressed. The text is greppable inside the file itself,
which means the consent record can be read back with `strings` if every
other copy of the terms disappears -- durability was the point.

Limits, stated plainly:

* Text only. No images, no links, no forms.
* WinAnsi (cp1252) characters only. Anything else becomes "?". Email
  addresses and the terms text are ASCII; a non-Latin display name would
  degrade, not fail.
* Wrapping is by character count against a conservative average glyph
  width, not by measured font metrics, so a line of wide characters can run
  slightly short of the margin. It cannot overflow it.
"""

import re
from datetime import UTC, datetime

# A4 in points.
PAGE_WIDTH = 595.28
PAGE_HEIGHT = 841.89
MARGIN = 56.0

# (font resource, size, leading, max characters per line)
# Character budgets come from the widest average glyph width of each face
# at each size against the 483pt text column, rounded down.
_STYLES: dict[str, tuple[str, int, int, int]] = {
    "h1": ("/F2", 15, 22, 56),
    "h2": ("/F2", 12, 18, 72),
    "body": ("/F1", 10, 13, 92),
    "mono": ("/F3", 9, 12, 88),
    "blank": ("/F1", 10, 8, 92),
}


def _escape(text: str) -> bytes:
    """A PDF literal string: cp1252 bytes with the three delimiters escaped."""
    raw = text.encode("cp1252", errors="replace")
    raw = raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    # Control characters have no glyph and can confuse a lenient parser.
    return bytes(b if b >= 0x20 or b == 0x09 else 0x3F for b in raw)


def _wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap. Words longer than the line (a SHA-256 hex digest,
    a long URL) are split hard rather than allowed to overflow the page."""
    lines: list[str] = []
    for paragraph in text.replace("\r\n", "\n").split("\n"):
        words = paragraph.split(" ")
        current = ""
        for word in words:
            while len(word) > width:
                if current:
                    lines.append(current)
                    current = ""
                lines.append(word[:width])
                word = word[width:]
            candidate = word if not current else f"{current} {word}"
            if len(candidate) <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _layout(blocks: list[tuple[str, str]]) -> list[list[tuple[str, int, float, str]]]:
    """Blocks -> pages of (font, size, y, line)."""
    pages: list[list[tuple[str, int, float, str]]] = []
    page: list[tuple[str, int, float, str]] = []
    y = PAGE_HEIGHT - MARGIN
    bottom = MARGIN

    for style, text in blocks:
        font, size, leading, width = _STYLES.get(style, _STYLES["body"])
        lines = [""] if style == "blank" else _wrap(text, width)
        for line in lines:
            if y - leading < bottom:
                pages.append(page)
                page = []
                y = PAGE_HEIGHT - MARGIN
            y -= leading
            if line:
                page.append((font, size, y, line))
    pages.append(page)
    return pages


def _content_stream(lines: list[tuple[str, int, float, str]]) -> bytes:
    parts = [b"BT"]
    current_font = None
    for font, size, y, line in lines:
        if (font, size) != current_font:
            parts.append(f"{font} {size} Tf".encode())
            current_font = (font, size)
        parts.append(f"1 0 0 1 {MARGIN:.2f} {y:.2f} Tm".encode())
        parts.append(b"(" + _escape(line) + b") Tj")
    parts.append(b"ET")
    return b"\n".join(parts) + b"\n"


def _pdf_date(moment: datetime) -> str:
    moment = moment.astimezone(UTC) if moment.tzinfo else moment
    return moment.strftime("D:%Y%m%d%H%M%SZ")


def build_pdf(
    blocks: list[tuple[str, str]],
    *,
    title: str,
    subject: str = "",
    keywords: str = "",
    created_at: datetime | None = None,
) -> bytes:
    """Render text blocks to PDF bytes.

    blocks is a list of (style, text) with style one of h1, h2, body, mono,
    blank. Text may contain newlines; each paragraph is wrapped independently.
    Metadata lands in the Info dictionary, where a reader's "properties"
    panel shows it -- the consent record puts its digest in `keywords` so it
    is visible without opening the page text.
    """
    pages = _layout(blocks)
    created_at = created_at or datetime.now(UTC)

    # Object numbers: 1 catalog, 2 pages, 3-5 fonts, 6 info, then one page
    # object and one content object per page.
    objects: list[bytes] = []
    page_object_numbers = [7 + 2 * index for index in range(len(pages))]
    kids = " ".join(f"{number} 0 R" for number in page_object_numbers)

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    for base_font in ("Helvetica", "Helvetica-Bold", "Courier"):
        objects.append(
            f"<< /Type /Font /Subtype /Type1 /BaseFont /{base_font} "
            "/Encoding /WinAnsiEncoding >>".encode()
        )
    objects.append(
        b"<< /Title ("
        + _escape(title)
        + b") /Subject ("
        + _escape(subject)
        + b") /Keywords ("
        + _escape(keywords)
        + b") /Producer (Mailyte pdf_minimal) /Creator (Mailyte) /CreationDate ("
        + _pdf_date(created_at).encode()
        + b") >>"
    )

    for index, lines in enumerate(pages):
        content_number = page_object_numbers[index] + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> "
            f"/Contents {content_number} 0 R >>".encode()
        )
        stream = _content_stream(lines)
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"endstream")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode()
    return bytes(out)


_TJ_RE = re.compile(rb"\((.*?)(?<!\\)\) Tj", re.DOTALL)


def extract_text(pdf: bytes) -> str:
    """The text of a PDF this module produced, one line per Tj operator.

    Only understands its own output (uncompressed streams, literal strings).
    Exists so tests -- and an operator with nothing but a Python shell -- can
    read a stored consent document back without a PDF library.
    """
    lines = []
    for match in _TJ_RE.finditer(pdf):
        raw = match.group(1)
        raw = raw.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
        lines.append(raw.decode("cp1252", errors="replace"))
    return "\n".join(lines)
