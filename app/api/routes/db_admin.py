from fastapi import APIRouter, Depends

from app.core.config import get_settings
from app.db.base_repository import BaseRepository
from app.db.factory import switch_repository
from app.api.deps import get_repo
from app.schemas.schemas import ConnectDbRequest, ConnectDbResponse, HealthResponse

router = APIRouter(tags=["admin"])


@router.post("/connect-db", response_model=ConnectDbResponse)
def connect_db(payload: ConnectDbRequest):
    """
    Tests and switches the active database backend at runtime. This
    replaces the process-wide repository singleton — in-flight jobs against
    the previous backend are unaffected (they hold their own repo reference
    from when they started), but everything after this call uses the new one.
    """
    try:
        new_repo = switch_repository(
            database_type=payload.database_type,
            connection_string=payload.connection_string,
            neo4j_uri=payload.neo4j_uri, neo4j_user=payload.neo4j_user, neo4j_password=payload.neo4j_password,
        )
        connected = new_repo.test_connection()
        return ConnectDbResponse(
            database_type=payload.database_type, connected=connected,
            message="Connected and switched active database backend." if connected else "Connected but a health check failed.",
        )
    except Exception as exc:
        return ConnectDbResponse(database_type=payload.database_type, connected=False, message=str(exc))


@router.get("/health", response_model=HealthResponse)
def health(repo: BaseRepository = Depends(get_repo)):
    settings = get_settings()
    connected = repo.test_connection()
    return HealthResponse(status="ok" if connected else "degraded",
                           database_type=settings.DATABASE_TYPE, database_connected=connected)
