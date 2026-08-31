"""Forms test fixtures: synthetic source PDFs with real text layers."""

from __future__ import annotations

import io

from pypdf import PdfWriter


def make_forms_pdf(pages: list[list[str]]) -> bytes:
    """Build a valid PDF; each page renders the given lines top-to-bottom."""
    writer = PdfWriter()
    for lines in pages:
        page = writer.add_blank_page(width=612, height=792)
        content = _render_lines(lines)
        # Multiple Tj operators with descending y so pypdf extracts real lines.
        page.merge_page(_content_page(content))
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _render_lines(lines: list[str]) -> str:
    parts = ["BT /F1 12 Tf 72 720 Td"]
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        parts.append(f"({escaped}) Tj 0 -14 Td")
    parts.append("ET")
    return " ".join(parts)


def _content_page(content: str) -> object:
    from pypdf import PdfReader

    single = io.BytesIO(_wrap_content(content))
    return PdfReader(single).pages[0]


def _wrap_content(content: str) -> bytes:
    return _make_single_page_pdf(content)


def _make_single_page_pdf(content: str) -> bytes:
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    content_bytes = content.encode()
    content_id = add(
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content_bytes), content_bytes)
    )
    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_id = add(
        b"<< /Type /Page /Parent 3 0 R /MediaBox [0 0 612 792] "
        + f"/Contents {content_id} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >>".encode()
        + b" >>"
    )
    pages_id = add(b"<< /Type /Pages /Kids [%d 0 R] /Count 1 >>" % page_id)
    catalog_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_at = out.tell()
    out.write(
        f"xref\n0 {len(objects) + 1}\n".encode()
        + b"0000000000 65535 f \n"
        + b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets[1:])
    )
    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>"
        f"\nstartxref\n{xref_at}\n%%EOF"
    )
    out.write(trailer.encode())
    return out.getvalue()


class FakeOcrEngine:
    """Deterministic stand-in for the tesseract CLI (no subprocess)."""

    def __init__(self, text: str, *, available: bool = True, fail: bool = False) -> None:
        self._text = text
        self._available = available
        self._fail = fail
        self.pages: list[int] = []

    def is_available(self) -> bool:
        return self._available

    def ocr_page(self, pdf_path: str, page_index: int) -> str:
        self.pages.append(page_index)
        if self._fail:
            raise RuntimeError("tesseract exploded")
        return self._text
