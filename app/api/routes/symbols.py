"""
Symbol dictionary — read side.

symbol_dictionary is written to from vlm-extraction-db's two teaching
endpoints (POST /vlm/teach-symbol and POST /vlm/extraction/{project_id}/
unknown-symbols/{unknown_id}/resolve, in app/api/routes/vlm_extraction_db.py),
both via the same `repo.add_symbol_dictionary_entry(...)` this router reads
back with. Because `repo` is the single process-wide connection switched by
POST /connect-db (see db_admin.py), whatever a human teaches through the
vlm-extraction-db workflow shows up here immediately — same database, same
table, no separate configuration.

This router used to also expose a job_id-keyed unknown-symbol list/label
pair (GET /jobs/{job_id}/unknown-symbols, POST /label-unknown-symbol) for
the CV/OCR job pipeline. That pipeline's job-creation endpoint was removed,
so those routes had no way to ever get a job_id — removed here too.
Unknown-symbol browsing and resolving for the surviving VLM pipeline lives
under vlm-extraction-db instead: GET /vlm/extraction/{project_id}/
unknown-symbols and POST /vlm/extraction/{project_id}/unknown-symbols/
{unknown_id}/resolve.
"""
from fastapi import APIRouter, Depends

from app.db.base_repository import BaseRepository
from app.api.deps import get_repo
from app.schemas.schemas import SymbolDictionaryEntry

router = APIRouter(tags=["symbols"])


@router.get("/symbols/dictionary", response_model=list[SymbolDictionaryEntry])
def get_symbol_dictionary(repo: BaseRepository = Depends(get_repo)):
    """
    Every symbol taught so far — via POST /vlm/teach-symbol or a resolved
    unknown symbol from the vlm-extraction-db workflow — on the currently
    connected database (POST /connect-db).
    """
    return repo.get_symbol_dictionary()
