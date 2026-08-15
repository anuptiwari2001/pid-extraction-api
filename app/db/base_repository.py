"""
Abstract repository interface. Every concrete backend (MSSQL, Postgres,
MySQL, Mongo, Neo4j) implements this same contract so the service layer
(app/services/extraction) never branches on DATABASE_TYPE — it just calls
`repo.save_extraction_result(...)` etc. and the repository handles the
storage-specific translation.

Design note: SQL backends (mssql/postgres/mysql) share one implementation
(SQLRepository in sql_repository.py) because the schema and query patterns
are identical across dialects — only the SQLAlchemy connection URL differs.
Mongo and Neo4j get their own implementations because the storage model
(documents / graph) is fundamentally different from a relational schema.
"""
from abc import ABC, abstractmethod
from typing import Any, Optional


class BaseRepository(ABC):
    """Contract every DB backend must fulfill."""

    # --- lifecycle ---
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def test_connection(self) -> bool: ...

    @abstractmethod
    def init_schema(self) -> None:
        """Create tables/collections/constraints if they don't exist."""
        ...

    # --- projects & jobs ---
    @abstractmethod
    def create_project(self, name: str, description: Optional[str] = None) -> dict: ...

    @abstractmethod
    def get_project(self, project_id: str) -> Optional[dict]: ...

    @abstractmethod
    def create_extraction_job(
        self, project_id: str, source_filenames: list[str],
        confidence_threshold: float, auto_learn_unknowns: bool
    ) -> dict: ...

    @abstractmethod
    def update_job_status(
        self, job_id: str, status: str, progress_pct: Optional[float] = None,
        error_message: Optional[str] = None
    ) -> None: ...

    @abstractmethod
    def get_job(self, job_id: str) -> Optional[dict]: ...

    # --- pages ---
    @abstractmethod
    def create_page(self, job_id: str, project_id: str, page_number: int,
                     source_filename: str, image_path: str, width_px: int, height_px: int) -> dict: ...

    @abstractmethod
    def update_page_status(self, page_id: str, status: str) -> None: ...

    @abstractmethod
    def get_pages_for_job(self, job_id: str) -> list[dict]: ...

    # --- extraction results ---
    @abstractmethod
    def save_symbols(self, page_id: str, symbols: list[dict]) -> list[dict]: ...

    @abstractmethod
    def get_symbols_for_page(self, page_id: str) -> list[dict]: ...

    @abstractmethod
    def save_instruments(self, project_id: str, page_id: str, instruments: list[dict]) -> list[dict]: ...

    @abstractmethod
    def save_equipment(self, project_id: str, page_id: str, equipment: list[dict]) -> list[dict]: ...

    @abstractmethod
    def save_lines(self, project_id: str, page_id: str, lines: list[dict]) -> list[dict]: ...

    @abstractmethod
    def save_annotations(self, project_id: str, page_id: str, annotations: list[dict]) -> list[dict]: ...

    @abstractmethod
    def save_relationships(self, project_id: str, page_id: str, relationships: list[dict]) -> list[dict]: ...

    @abstractmethod
    def get_job_result(self, job_id: str) -> dict:
        """Full structured result: pages + entities + relationships for a job."""
        ...

    # --- human-in-the-loop ---
    @abstractmethod
    def save_unknown_symbol(self, job_id: str, page_id: str, page_number: int,
                             symbol_id: Optional[str], bbox: dict, crop_image_path: str,
                             surrounding_text: Optional[str], original_confidence: Optional[float]) -> dict: ...

    @abstractmethod
    def get_unknown_symbol(self, unknown_symbol_id: str) -> Optional[dict]: ...

    @abstractmethod
    def get_pending_unknown_symbols(self, job_id: str) -> list[dict]: ...

    @abstractmethod
    def resolve_unknown_symbol(self, unknown_symbol_id: str, category_name: str,
                                attributes: dict) -> dict: ...

    @abstractmethod
    def add_symbol_dictionary_entry(self, category_name: str, source: str,
                                     isa_type_code: Optional[str] = None,
                                     description: Optional[str] = None,
                                     reference_crop_path: Optional[str] = None,
                                     shape_signature: Optional[Any] = None,
                                     attributes_schema: Optional[dict] = None) -> dict: ...

    @abstractmethod
    def get_symbol_dictionary(self) -> list[dict]: ...

    @abstractmethod
    def find_symbol_dictionary_by_signature(self, shape_signature: Any) -> Optional[dict]:
        """Cheap geometric-signature lookup, used to auto-resolve previously
        labeled unknown symbols without asking a human twice."""
        ...

    # --- queries ---
    @abstractmethod
    def get_project_tags(self, project_id: str) -> list[dict]: ...

    @abstractmethod
    def find_duplicate_tags(self, project_id: Optional[str] = None) -> list[dict]:
        """Used by the standalone duplicate-tag-finder utility."""
        ...
