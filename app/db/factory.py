"""
Repository factory. This is the ONE place in the app that knows how to turn
DATABASE_TYPE + connection settings into a concrete BaseRepository. Every
other module depends only on the BaseRepository interface.

A process-wide singleton is cached so repeated `get_repository()` calls
(e.g. across FastAPI dependency injection) reuse the same connection pool /
driver instance, and swapped out wholesale by `switch_repository()` when
POST /connect-db is used to change backends at runtime.
"""
from typing import Optional

from app.core.config import get_settings
from app.core.errors import ValidationErrorApp
from app.db.base_repository import BaseRepository
from app.core.logging_config import get_logger

logger = get_logger(__name__)

_SUPPORTED = {"mssql", "postgres", "mysql", "mongo", "neo4j"}

_repository_singleton: Optional[BaseRepository] = None


def _build_sql_repository(database_type: str, connection_string: str) -> BaseRepository:
    from app.db.sql_repository import SQLRepository
    return SQLRepository(database_url=connection_string, dialect=database_type)


def _build_mongo_repository(connection_string: str) -> BaseRepository:
    from app.db.mongo_repository import MongoRepository
    return MongoRepository(connection_string=connection_string)


def _build_neo4j_repository(uri: str, user: str, password: str) -> BaseRepository:
    from app.db.neo4j_repository import Neo4jRepository
    return Neo4jRepository(uri=uri, user=user, password=password)


def build_repository(
    database_type: str,
    connection_string: Optional[str] = None,
    neo4j_uri: Optional[str] = None,
    neo4j_user: Optional[str] = None,
    neo4j_password: Optional[str] = None,
) -> BaseRepository:
    database_type = database_type.lower()
    if database_type not in _SUPPORTED:
        raise ValidationErrorApp(
            f"Unsupported DATABASE_TYPE '{database_type}'", {"supported": sorted(_SUPPORTED)}
        )

    settings = get_settings()

    if database_type in ("mssql", "postgres", "mysql"):
        conn = connection_string or settings.DATABASE_URL
        repo = _build_sql_repository(database_type, conn)
    elif database_type == "mongo":
        conn = connection_string or settings.DATABASE_URL
        repo = _build_mongo_repository(conn)
    else:  # neo4j
        repo = _build_neo4j_repository(
            uri=neo4j_uri or settings.NEO4J_URI,
            user=neo4j_user or settings.NEO4J_USER,
            password=neo4j_password or settings.NEO4J_PASSWORD,
        )

    repo.connect()
    repo.init_schema()
    logger.info("repository_built", extra={"context": {"database_type": database_type}})
    return repo


def get_repository() -> BaseRepository:
    global _repository_singleton
    if _repository_singleton is None:
        settings = get_settings()
        _repository_singleton = build_repository(settings.DATABASE_TYPE)
    return _repository_singleton


def switch_repository(
    database_type: str,
    connection_string: Optional[str] = None,
    neo4j_uri: Optional[str] = None,
    neo4j_user: Optional[str] = None,
    neo4j_password: Optional[str] = None,
) -> BaseRepository:
    """Used by POST /connect-db to hot-swap the active backend at runtime."""
    global _repository_singleton
    new_repo = build_repository(database_type, connection_string, neo4j_uri, neo4j_user, neo4j_password)
    _repository_singleton = new_repo
    return _repository_singleton
