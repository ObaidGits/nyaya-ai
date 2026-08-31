"""Upload validation (REQUIREMENTS D-044..D-048; ARCHITECTURE §20).

Validation is content-based, not filename-based (D-046): the upload is
sniffed for the ``%PDF-`` magic, opened with pypdf, and rejected when
encrypted or unreadable (D-047/D-048). Rejections use client-safe
application errors — no parser internals leak out.
"""

from __future__ import annotations

import io

from app.core.errors import AppError

MAX_FILENAME_LENGTH = 255


class UploadRejectedError(AppError):
    """An uploaded file failed validation."""

    status_code = 400
    code = "UPLOAD_REJECTED"


def _reject(message: str, *, code: str = "UPLOAD_REJECTED") -> None:
    raise UploadRejectedError(message, code=code)


def validate_upload(
    *,
    filename: str,
    content_type: str | None,
    data: bytes,
    allowed_types: set[str],
    max_size_bytes: int,
) -> None:
    """Validate one uploaded file before it is stored.

    Raises:
        UploadRejectedError: with a client-safe message and code.
    """
    if not filename or len(filename) > MAX_FILENAME_LENGTH:
        _reject("A valid filename is required.", code="INVALID_FILENAME")
    if any(ord(char) < 32 or ord(char) == 127 for char in filename):
        # Control characters (incl. NUL) never appear in legitimate filenames
        # and can confuse downstream tooling.
        _reject("A valid filename is required.", code="INVALID_FILENAME")

    if not data:
        _reject("The uploaded file is empty.", code="EMPTY_FILE")

    if len(data) > max_size_bytes:
        _reject(
            f"The uploaded file exceeds the maximum size of {max_size_bytes // (1024 * 1024)} MB.",
            code="FILE_TOO_LARGE",
        )

    # Allowlist on both the declared content type and the extension (D-044);
    # the MIME sniff below is the authoritative check (D-046).
    unsupported = "Unsupported file type. Only PDF documents can be uploaded."
    if allowed_types and content_type is not None and content_type not in allowed_types:
        _reject(unsupported, code="UNSUPPORTED_TYPE")
    if allowed_types and not filename.lower().endswith(".pdf"):
        _reject(unsupported, code="UNSUPPORTED_TYPE")

    # MIME sniffing: the payload must actually be a PDF (D-046).
    if not data.startswith(b"%PDF-"):
        _reject("The uploaded file is not a valid PDF document.", code="INVALID_PDF")

    _validate_pdf_readable(data)


def _validate_pdf_readable(data: bytes) -> None:
    """Open the PDF and reject encrypted or corrupt documents (D-047/D-048)."""
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:  # pragma: no cover - pypdf is a core dependency
        _reject("PDF validation is unavailable.", code="PDF_VALIDATION_UNAVAILABLE")
        return

    try:
        reader = PdfReader(io.BytesIO(data))
        encrypted = reader.is_encrypted
        page_count = len(reader.pages) if not encrypted else 0
    except UploadRejectedError:
        raise
    except PdfReadError:
        _reject("The uploaded PDF is corrupt or unreadable.", code="CORRUPT_PDF")
        return
    except (ValueError, KeyError):
        _reject("The uploaded PDF is corrupt or unreadable.", code="CORRUPT_PDF")
        return
    except Exception:
        # Unknown parser failures are still upload rejections, never 500s
        # with internals attached.
        _reject("The uploaded PDF could not be read.", code="INVALID_PDF")
        return
    if encrypted:
        _reject("Encrypted PDF documents cannot be processed.", code="ENCRYPTED_PDF")
    _ = page_count
