"""User-document test fixtures: real PDFs built with pypdf."""

from __future__ import annotations

import io
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def make_pdf(pages: list[str]) -> bytes:
    """Build a minimal valid PDF whose pages carry the given text."""
    return _make_pdf(pages)


def _make_pdf(pages: list[str]) -> bytes:
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    page_ids: list[int] = []
    for text in pages:
        stream = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        content = f"BT /F1 12 Tf 72 720 Td ({stream}) Tj ET".encode()
        content_id = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content))
        font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        page_id = add(
            b"<< /Type /Page /Parent 3 0 R /MediaBox [0 0 612 792] "
            + f"/Contents {content_id} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >>".encode()
            + b" >>"
        )
        page_ids.append(page_id)

    pages_id = add(
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{pid} 0 R".encode() for pid in page_ids)
        + b"] /Count %d >>" % len(page_ids)
    )
    # The Pages object must be object 3 for the /Parent references above.
    catalog = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id
    catalog_id = add(catalog)

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
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>"
    out.write(f"{trailer}\nstartxref\n{xref_at}\n%%EOF".encode())
    return out.getvalue()


def make_encrypted_pdf() -> bytes:
    """Build an encrypted PDF (rejected per D-047)."""
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.encrypt(user_password="secret")
    writer.write(buffer)
    return buffer.getvalue()


def make_valid_pdf_pages(data: bytes) -> int:
    """Sanity helper: how many pages a generated PDF has."""
    return len(PdfReader(io.BytesIO(data)).pages)


def assert_valid_pdf(data: bytes, tmp_path: Path) -> list[str]:
    """Extract page texts through the production extractor."""
    from app.ingestion.extract import PypdfPageExtractor

    path = tmp_path / "probe.pdf"
    path.write_bytes(data)
    pages = PypdfPageExtractor().extract(str(path))
    return ["\n".join(p.lines) for p in pages]
