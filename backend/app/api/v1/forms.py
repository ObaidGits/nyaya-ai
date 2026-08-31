"""Forms API endpoints (REQUIREMENTS B-033..B-040, D-021..D-024; §31/§38).

The endpoints serve the generated forms library. Extraction runs out-of-band
(``scripts/extract_forms.py``); when no library is configured the endpoints
fail closed with 503 ``FORMS_NOT_CONFIGURED``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from app.forms.library import FormsLibrary, build_forms_library
from app.forms.models import FormListItem, FormMetadata

router = APIRouter(prefix="/forms", tags=["forms"])


def get_forms_library(request: Request) -> FormsLibrary:
    from typing import cast

    library = cast(FormsLibrary | None, getattr(request.app.state, "forms_library", None))
    if library is None:
        from app.core.errors import AppError

        raise AppError(
            "The forms library is not configured on this instance.",
            status_code=503,
            code="FORMS_NOT_CONFIGURED",
        )
    return library


@router.get("")
def list_forms(
    library: Annotated[FormsLibrary, Depends(get_forms_library)],
    needs_review: Annotated[bool | None, Query()] = None,
) -> list[FormListItem]:
    """List every extracted form (B-033..B-037)."""
    return library.list_forms(needs_review=needs_review)


@router.get("/search")
def search_forms(
    library: Annotated[FormsLibrary, Depends(get_forms_library)],
    q: Annotated[str, Query(min_length=1, max_length=200)],
) -> list[FormListItem]:
    """Search forms by title or number (B-040)."""
    normalized = q.strip()
    if not normalized:
        # Whitespace-only queries would match every form; reject like empty.
        from app.core.errors import AppError

        raise AppError(
            "Search query must contain non-whitespace characters.",
            status_code=422,
            code="VALIDATION_ERROR",
        )
    return library.list_forms(query=normalized)


@router.get("/download-all")
def download_all_forms(
    library: Annotated[FormsLibrary, Depends(get_forms_library)],
) -> Response:
    """Bulk download: every form plus the manifest in one ZIP (B-039)."""
    data = library.build_zip()
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="forms_library.zip"'},
    )


@router.get("/{form_number}")
def get_form_metadata(
    form_number: int,
    library: Annotated[FormsLibrary, Depends(get_forms_library)],
) -> FormMetadata:
    """Retrieve one form's metadata (title, pages, hash, confidence)."""
    return library.get_metadata(form_number)


@router.get("/{form_number}/download")
def download_form(
    form_number: int,
    library: Annotated[FormsLibrary, Depends(get_forms_library)],
) -> Response:
    """Download one page-perfect form PDF (B-038)."""
    filename, data = library.read_form(form_number)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


__all__ = [
    "build_forms_library",
    "get_forms_library",
    "router",
]
