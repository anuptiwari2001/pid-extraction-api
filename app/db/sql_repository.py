"""
SQLAlchemy-backed repository. One implementation serves MSSQL, PostgreSQL,
and MySQL — the only thing that changes between them is the connection URL
(and its driver), which SQLAlchemy abstracts away. Dialect-specific quirks
(if any crop up later — e.g. MSSQL's handling of large JSON columns) should
be isolated behind small `if self.dialect == "mssql":` branches rather than
forking the whole class.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from app.db.base_repository import BaseRepository
from app.models.orm import (
    Base, Project, ExtractionJob, Page, Symbol, Instrument, Equipment,
    Line, Annotation, RelationshipEdge, SymbolDictionary, UnknownSymbol,
)
from app.core.errors import DatabaseConnectionError, NotFoundError
from app.core.logging_config import get_logger

logger = get_logger(__name__)


def _row_to_dict(obj) -> dict:
    if obj is None:
        return None
    d = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    return d


class SQLRepository(BaseRepository):
    def __init__(self, database_url: str, dialect: str = "mssql"):
        self.database_url = database_url
        self.dialect = dialect
        self.engine = None
        self.SessionLocal: Optional[sessionmaker] = None

    def connect(self) -> None:
        try:
            # Add connection pool and timeout settings to prevent hanging connections.
            # pool_size: max number of cached connections per pool
            # max_overflow: max temporary connections above pool_size
            # pool_timeout: seconds to wait for a connection from the pool before raising
            # connect_args varies by dialect:
            #   - MSSQL: "timeout" in seconds (connection timeout)
            #   - Postgres: "connect_timeout" in seconds
            #   - MySQL: "connect_timeout" in seconds
            connect_args = {}
            pool_size = 10
            max_overflow = 20
            pool_timeout = 30

            if self.dialect == "mssql":
                # MSSQL: set timeout to 30 seconds for initial connection
                connect_args["timeout"] = 30
            elif self.dialect == "postgres":
                # Postgres: set connect_timeout to 30 seconds
                connect_args["connect_timeout"] = 30
            elif self.dialect == "mysql":
                # MySQL: set connect_timeout to 30 seconds
                connect_args["connect_timeout"] = 30

            self.engine = create_engine(
                self.database_url,
                pool_pre_ping=True,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=pool_timeout,
                connect_args=connect_args,
                future=True,
            )
            self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info(
                "db_connected",
                extra={
                    "context": {
                        "dialect": self.dialect,
                        "pool_size": pool_size,
                        "max_overflow": max_overflow,
                        "pool_timeout": pool_timeout,
                    }
                },
            )
        except Exception as exc:
            raise DatabaseConnectionError(f"Failed to connect to {self.dialect} database", {"error": str(exc)})

    def test_connection(self) -> bool:
        try:
            if self.engine is None:
                self.connect()
            with self.engine.connect():
                return True
        except Exception:
            return False

    def init_schema(self) -> None:
        if self.engine is None:
            self.connect()
        Base.metadata.create_all(self.engine)
        logger.info("schema_initialized", extra={"context": {"dialect": self.dialect}})

    def _session(self) -> Session:
        if self.SessionLocal is None:
            self.connect()
        return self.SessionLocal()

    # --- projects & jobs ---

    def create_project(self, name: str, description: Optional[str] = None) -> dict:
        with self._session() as s:
            p = Project(name=name, description=description)
            s.add(p)
            s.commit()
            s.refresh(p)
            return _row_to_dict(p)

    def get_project(self, project_id: str) -> Optional[dict]:
        with self._session() as s:
            return _row_to_dict(s.get(Project, project_id))

    def create_extraction_job(self, project_id: str, source_filenames: list[str],
                               confidence_threshold: float, auto_learn_unknowns: bool) -> dict:
        with self._session() as s:
            job = ExtractionJob(
                project_id=project_id,
                source_filenames=source_filenames,
                confidence_threshold=confidence_threshold,
                auto_learn_unknowns=auto_learn_unknowns,
                status="queued",
                progress_pct=0.0,
            )
            s.add(job)
            s.commit()
            s.refresh(job)
            return _row_to_dict(job)

    def update_job_status(self, job_id: str, status: str, progress_pct: Optional[float] = None,
                           error_message: Optional[str] = None) -> None:
        with self._session() as s:
            job = s.get(ExtractionJob, job_id)
            if not job:
                raise NotFoundError(f"Job {job_id} not found")
            job.status = status
            if progress_pct is not None:
                job.progress_pct = progress_pct
            if error_message is not None:
                job.error_message = error_message
            job.updated_at = datetime.now(timezone.utc)
            s.commit()

    def get_job(self, job_id: str) -> Optional[dict]:
        with self._session() as s:
            return _row_to_dict(s.get(ExtractionJob, job_id))

    # --- pages ---

    def create_page(self, job_id: str, project_id: str, page_number: int, source_filename: str,
                     image_path: str, width_px: int, height_px: int) -> dict:
        with self._session() as s:
            page = Page(
                job_id=job_id, project_id=project_id, page_number=page_number,
                source_filename=source_filename, image_path=image_path,
                width_px=width_px, height_px=height_px, status="pending",
            )
            s.add(page)
            s.commit()
            s.refresh(page)
            return _row_to_dict(page)

    def update_page_status(self, page_id: str, status: str) -> None:
        with self._session() as s:
            page = s.get(Page, page_id)
            if not page:
                raise NotFoundError(f"Page {page_id} not found")
            page.status = status
            s.commit()

    def get_pages_for_job(self, job_id: str) -> list[dict]:
        with self._session() as s:
            pages = s.query(Page).filter(Page.job_id == job_id).order_by(Page.page_number).all()
            return [_row_to_dict(p) for p in pages]

    # --- extraction results ---

    def save_symbols(self, page_id: str, symbols: list[dict]) -> list[dict]:
        with self._session() as s:
            rows = []
            for sym in symbols:
                row = Symbol(
                    page_id=page_id,
                    class_name=sym["class_name"],
                    confidence=sym["confidence"],
                    bbox_x1=sym["bbox"]["x1"], bbox_y1=sym["bbox"]["y1"],
                    bbox_x2=sym["bbox"]["x2"], bbox_y2=sym["bbox"]["y2"],
                    extracted_text=sym.get("extracted_text"),
                    is_unknown=sym.get("is_unknown", False),
                )
                s.add(row)
                rows.append(row)
            s.commit()
            for r in rows:
                s.refresh(r)
            return [_row_to_dict(r) for r in rows]

    def get_symbols_for_page(self, page_id: str) -> list[dict]:
        with self._session() as s:
            rows = s.query(Symbol).filter(Symbol.page_id == page_id).all()
            return [_row_to_dict(r) for r in rows]

    def save_instruments(self, project_id: str, page_id: str, instruments: list[dict]) -> list[dict]:
        with self._session() as s:
            rows = []
            for inst in instruments:
                row = Instrument(
                    project_id=project_id, page_id=page_id, symbol_id=inst.get("symbol_id"),
                    tag=inst.get("tag"), isa_type_code=inst.get("isa_type_code"),
                    instrument_type=inst.get("instrument_type"), location=inst.get("location"),
                    connected_to=inst.get("connected_to", []), attributes=inst.get("attributes", {}),
                )
                s.add(row)
                rows.append(row)
            s.commit()
            for r in rows:
                s.refresh(r)
            return [_row_to_dict(r) for r in rows]

    def save_equipment(self, project_id: str, page_id: str, equipment: list[dict]) -> list[dict]:
        with self._session() as s:
            rows = []
            for eq in equipment:
                row = Equipment(
                    project_id=project_id, page_id=page_id, symbol_id=eq.get("symbol_id"),
                    tag=eq.get("tag"), equipment_type=eq.get("equipment_type"),
                    bbox_x1=eq.get("bbox", {}).get("x1"), bbox_y1=eq.get("bbox", {}).get("y1"),
                    bbox_x2=eq.get("bbox", {}).get("x2"), bbox_y2=eq.get("bbox", {}).get("y2"),
                    attributes=eq.get("attributes", {}),
                )
                s.add(row)
                rows.append(row)
            s.commit()
            for r in rows:
                s.refresh(r)
            return [_row_to_dict(r) for r in rows]

    def save_lines(self, project_id: str, page_id: str, lines: list[dict]) -> list[dict]:
        with self._session() as s:
            rows = []
            for ln in lines:
                row = Line(
                    project_id=project_id, page_id=page_id, line_number=ln.get("line_number"),
                    line_type=ln.get("line_type"), from_tag=ln.get("from_tag"), to_tag=ln.get("to_tag"),
                    path_points=ln.get("path_points", []), attributes=ln.get("attributes", {}),
                )
                s.add(row)
                rows.append(row)
            s.commit()
            for r in rows:
                s.refresh(r)
            return [_row_to_dict(r) for r in rows]

    def save_annotations(self, project_id: str, page_id: str, annotations: list[dict]) -> list[dict]:
        with self._session() as s:
            rows = []
            for an in annotations:
                row = Annotation(
                    project_id=project_id, page_id=page_id, text=an["text"],
                    bbox_x1=an.get("bbox", {}).get("x1"), bbox_y1=an.get("bbox", {}).get("y1"),
                    bbox_x2=an.get("bbox", {}).get("x2"), bbox_y2=an.get("bbox", {}).get("y2"),
                )
                s.add(row)
                rows.append(row)
            s.commit()
            for r in rows:
                s.refresh(r)
            return [_row_to_dict(r) for r in rows]

    def save_relationships(self, project_id: str, page_id: str, relationships: list[dict]) -> list[dict]:
        with self._session() as s:
            rows = []
            for rel in relationships:
                row = RelationshipEdge(
                    project_id=project_id, page_id=page_id,
                    source_entity_id=rel["source_entity_id"], source_entity_type=rel["source_entity_type"],
                    target_entity_id=rel["target_entity_id"], target_entity_type=rel["target_entity_type"],
                    relation_type=rel["relation_type"], confidence=rel.get("confidence", 1.0),
                    inferred_by=rel.get("inferred_by", "rule_based"),
                )
                s.add(row)
                rows.append(row)
            s.commit()
            for r in rows:
                s.refresh(r)
            return [_row_to_dict(r) for r in rows]

    def get_job_result(self, job_id: str) -> dict:
        with self._session() as s:
            job = s.get(ExtractionJob, job_id)
            if not job:
                raise NotFoundError(f"Job {job_id} not found")
            pages = s.query(Page).filter(Page.job_id == job_id).order_by(Page.page_number).all()

            page_results = []
            all_relationships = []
            for page in pages:
                symbols = s.query(Symbol).filter(Symbol.page_id == page.id).all()
                instruments = s.query(Instrument).filter(Instrument.page_id == page.id).all()
                equipment = s.query(Equipment).filter(Equipment.page_id == page.id).all()
                lines = s.query(Line).filter(Line.page_id == page.id).all()
                annotations = s.query(Annotation).filter(Annotation.page_id == page.id).all()
                rels = s.query(RelationshipEdge).filter(RelationshipEdge.page_id == page.id).all()
                all_relationships.extend(_row_to_dict(r) for r in rels)

                page_results.append({
                    "page_id": page.id,
                    "page_number": page.page_number,
                    "source_filename": page.source_filename,
                    "symbols": [_row_to_dict(x) for x in symbols],
                    "instruments": [_row_to_dict(x) for x in instruments],
                    "equipment": [_row_to_dict(x) for x in equipment],
                    "lines": [_row_to_dict(x) for x in lines],
                    "annotations": [_row_to_dict(x) for x in annotations],
                })

            return {
                "job_id": job.id,
                "project_id": job.project_id,
                "status": job.status,
                "pages": page_results,
                "relationships": all_relationships,
            }

    # --- human-in-the-loop ---

    def save_unknown_symbol(self, job_id: str, page_id: str, page_number: int,
                             symbol_id: Optional[str], bbox: dict, crop_image_path: str,
                             surrounding_text: Optional[str], original_confidence: Optional[float]) -> dict:
        with self._session() as s:
            row = UnknownSymbol(
                job_id=job_id, page_id=page_id, symbol_id=symbol_id, page_number=page_number,
                bbox_x1=bbox["x1"], bbox_y1=bbox["y1"], bbox_x2=bbox["x2"], bbox_y2=bbox["y2"],
                crop_image_path=crop_image_path, surrounding_text=surrounding_text,
                original_confidence=original_confidence, status="pending",
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return _row_to_dict(row)

    def get_unknown_symbol(self, unknown_symbol_id: str) -> Optional[dict]:
        with self._session() as s:
            return _row_to_dict(s.get(UnknownSymbol, unknown_symbol_id))

    def get_pending_unknown_symbols(self, job_id: str) -> list[dict]:
        with self._session() as s:
            rows = s.query(UnknownSymbol).filter(
                UnknownSymbol.job_id == job_id, UnknownSymbol.status == "pending"
            ).all()
            return [_row_to_dict(r) for r in rows]

    def resolve_unknown_symbol(self, unknown_symbol_id: str, category_name: str, attributes: dict) -> dict:
        with self._session() as s:
            row = s.get(UnknownSymbol, unknown_symbol_id)
            if not row:
                raise NotFoundError(f"Unknown symbol {unknown_symbol_id} not found")
            row.status = "labeled"
            row.user_provided_category = category_name
            row.user_provided_attributes = attributes
            row.resolved_at = datetime.now(timezone.utc)

            if row.symbol_id:
                sym = s.get(Symbol, row.symbol_id)
                if sym:
                    sym.class_name = category_name
                    sym.is_unknown = False
                    sym.resolved_from_unknown_id = row.id

            s.commit()
            s.refresh(row)
            return _row_to_dict(row)

    def add_symbol_dictionary_entry(self, category_name: str, source: str,
                                     isa_type_code: Optional[str] = None,
                                     description: Optional[str] = None,
                                     reference_crop_path: Optional[str] = None,
                                     shape_signature: Optional[Any] = None,
                                     attributes_schema: Optional[dict] = None) -> dict:
        with self._session() as s:
            existing = s.query(SymbolDictionary).filter(
                SymbolDictionary.category_name == category_name
            ).first()
            if existing:
                return _row_to_dict(existing)
            row = SymbolDictionary(
                category_name=category_name, source=source, isa_type_code=isa_type_code,
                description=description, reference_crop_path=reference_crop_path,
                shape_signature=shape_signature, attributes_schema=attributes_schema or {},
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return _row_to_dict(row)

    def get_symbol_dictionary(self) -> list[dict]:
        with self._session() as s:
            rows = s.query(SymbolDictionary).order_by(SymbolDictionary.category_name).all()
            return [_row_to_dict(r) for r in rows]

    def find_symbol_dictionary_by_signature(self, shape_signature: Any) -> Optional[dict]:
        # Simple exact/near match over stored signatures; see services/cv/symbol_signature.py
        from app.services.cv.symbol_signature import signatures_match
        with self._session() as s:
            rows = s.query(SymbolDictionary).filter(SymbolDictionary.source == "user_labeled").all()
            for r in rows:
                if r.shape_signature and signatures_match(r.shape_signature, shape_signature):
                    return _row_to_dict(r)
            return None

    # --- queries ---

    def get_project_tags(self, project_id: str) -> list[dict]:
        with self._session() as s:
            tags = []
            for inst in s.query(Instrument).filter(Instrument.project_id == project_id, Instrument.tag.isnot(None)):
                tags.append({"tag": inst.tag, "entity_type": "instrument", "entity_id": inst.id, "page_id": inst.page_id})
            for eq in s.query(Equipment).filter(Equipment.project_id == project_id, Equipment.tag.isnot(None)):
                tags.append({"tag": eq.tag, "entity_type": "equipment", "entity_id": eq.id, "page_id": eq.page_id})
            for ln in s.query(Line).filter(Line.project_id == project_id, Line.line_number.isnot(None)):
                tags.append({"tag": ln.line_number, "entity_type": "line", "entity_id": ln.id, "page_id": ln.page_id})
            return tags

    def find_duplicate_tags(self, project_id: Optional[str] = None) -> list[dict]:
        with self._session() as s:
            q_inst = s.query(Instrument.tag, Instrument.id, Instrument.page_id, Instrument.project_id)
            q_eq = s.query(Equipment.tag, Equipment.id, Equipment.page_id, Equipment.project_id)
            if project_id:
                q_inst = q_inst.filter(Instrument.project_id == project_id)
                q_eq = q_eq.filter(Equipment.project_id == project_id)

            tag_map: dict[str, list[dict]] = {}
            for tag, eid, page_id, proj_id in q_inst.all():
                if not tag:
                    continue
                tag_map.setdefault(tag, []).append(
                    {"entity_id": eid, "entity_type": "instrument", "page_id": page_id, "project_id": proj_id}
                )
            for tag, eid, page_id, proj_id in q_eq.all():
                if not tag:
                    continue
                tag_map.setdefault(tag, []).append(
                    {"entity_id": eid, "entity_type": "equipment", "page_id": page_id, "project_id": proj_id}
                )

            duplicates = [
                {"tag": tag, "occurrences": occurrences}
                for tag, occurrences in tag_map.items()
                if len(occurrences) > 1
            ]
            return duplicates
