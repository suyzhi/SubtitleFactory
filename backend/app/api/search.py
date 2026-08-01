"""Project-library subtitle search routes."""

from fastapi import APIRouter, Query

from ..services.search_index import search_segments


router = APIRouter(prefix="/api")


@router.get("/search/segments")
def segment_search(
    q: str = Query("", max_length=500),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    project_id: str = "",
    group_name: str = "",
    speaker_id: str = "",
    source_language: str = "",
    target_language: str = "",
    created_from: str = "",
    created_to: str = "",
):
    return search_segments(
        q,
        page=page,
        page_size=page_size,
        project_id=project_id,
        group_name=group_name,
        speaker_id=speaker_id,
        source_language=source_language,
        target_language=target_language,
        created_from=created_from,
        created_to=created_to,
    )
