from fastapi import APIRouter, Depends, Query

from app.db.base_repository import BaseRepository
from app.api.deps import get_repo

router = APIRouter(tags=["projects"])


@router.get("/projects/{project_id}/tags")
def get_project_tags(project_id: str, repo: BaseRepository = Depends(get_repo)):
    return {"project_id": project_id, "tags": repo.get_project_tags(project_id)}


@router.get("/tags/duplicates")
def find_duplicate_tags(
    project_id: str | None = Query(default=None, description="Restrict to one project; omit to scan the entire database"),
    repo: BaseRepository = Depends(get_repo),
):
    """
    Standalone duplicate-tag-finder utility. Scans instruments + equipment
    (and, on SQL backends, lines) for tags that occur more than once, either
    within one project or across the entire database — useful for catching
    tag-numbering errors across a multi-drawing FEED/EPC package.
    """
    duplicates = repo.find_duplicate_tags(project_id)
    return {"project_id": project_id, "duplicate_count": len(duplicates), "duplicates": duplicates}
