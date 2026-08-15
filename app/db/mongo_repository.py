"""
MongoDB repository. Requires `pymongo`. Stores each table as a collection
of documents rather than relational rows — no joins, so get_job_result()
does the assembly in Python instead of SQL.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.db.base_repository import BaseRepository
from app.core.errors import DatabaseConnectionError, NotFoundError
from app.core.logging_config import get_logger

logger = get_logger(__name__)


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MongoRepository(BaseRepository):
    def __init__(self, connection_string: str, db_name: str = "pid_extraction"):
        self.connection_string = connection_string
        self.db_name = db_name
        self.client = None
        self.db = None

    def connect(self) -> None:
        try:
            import pymongo
            self.client = pymongo.MongoClient(self.connection_string, serverSelectionTimeoutMS=5000)
            self.client.admin.command("ping")
            self.db = self.client[self.db_name]
            logger.info("db_connected", extra={"context": {"dialect": "mongo"}})
        except Exception as exc:
            raise DatabaseConnectionError("Failed to connect to MongoDB", {"error": str(exc)})

    def test_connection(self) -> bool:
        try:
            if self.client is None:
                self.connect()
            self.client.admin.command("ping")
            return True
        except Exception:
            return False

    def init_schema(self) -> None:
        if self.db is None:
            self.connect()
        # Mongo is schemaless; just ensure useful indexes exist.
        self.db.instruments.create_index("tag")
        self.db.equipment.create_index("tag")
        self.db.lines.create_index("line_number")
        self.db.symbol_dictionary.create_index("category_name", unique=True)
        logger.info("schema_initialized", extra={"context": {"dialect": "mongo"}})

    # --- projects & jobs ---

    def create_project(self, name: str, description: Optional[str] = None) -> dict:
        doc = {"_id": _uuid(), "name": name, "description": description, "created_at": _now()}
        self.db.projects.insert_one(doc)
        return doc

    def get_project(self, project_id: str) -> Optional[dict]:
        return self.db.projects.find_one({"_id": project_id})

    def create_extraction_job(self, project_id: str, source_filenames: list[str],
                               confidence_threshold: float, auto_learn_unknowns: bool) -> dict:
        doc = {
            "_id": _uuid(), "project_id": project_id, "status": "queued", "progress_pct": 0.0,
            "confidence_threshold": confidence_threshold, "auto_learn_unknowns": auto_learn_unknowns,
            "source_filenames": source_filenames, "error_message": None,
            "created_at": _now(), "updated_at": _now(),
        }
        self.db.extraction_jobs.insert_one(doc)
        return doc

    def update_job_status(self, job_id: str, status: str, progress_pct: Optional[float] = None,
                           error_message: Optional[str] = None) -> None:
        update = {"status": status, "updated_at": _now()}
        if progress_pct is not None:
            update["progress_pct"] = progress_pct
        if error_message is not None:
            update["error_message"] = error_message
        result = self.db.extraction_jobs.update_one({"_id": job_id}, {"$set": update})
        if result.matched_count == 0:
            raise NotFoundError(f"Job {job_id} not found")

    def get_job(self, job_id: str) -> Optional[dict]:
        return self.db.extraction_jobs.find_one({"_id": job_id})

    # --- pages ---

    def create_page(self, job_id: str, project_id: str, page_number: int, source_filename: str,
                     image_path: str, width_px: int, height_px: int) -> dict:
        doc = {
            "_id": _uuid(), "job_id": job_id, "project_id": project_id, "page_number": page_number,
            "source_filename": source_filename, "image_path": image_path,
            "width_px": width_px, "height_px": height_px, "status": "pending",
        }
        self.db.pages.insert_one(doc)
        return doc

    def update_page_status(self, page_id: str, status: str) -> None:
        result = self.db.pages.update_one({"_id": page_id}, {"$set": {"status": status}})
        if result.matched_count == 0:
            raise NotFoundError(f"Page {page_id} not found")

    def get_pages_for_job(self, job_id: str) -> list[dict]:
        return list(self.db.pages.find({"job_id": job_id}).sort("page_number", 1))

    # --- extraction results ---

    def _save_many(self, collection: str, docs: list[dict], extra: dict) -> list[dict]:
        rows = []
        for d in docs:
            row = {"_id": _uuid(), **extra, **d}
            rows.append(row)
        if rows:
            self.db[collection].insert_many(rows)
        return rows

    def save_symbols(self, page_id: str, symbols: list[dict]) -> list[dict]:
        return self._save_many("symbols", symbols, {"page_id": page_id})

    def get_symbols_for_page(self, page_id: str) -> list[dict]:
        return list(self.db.symbols.find({"page_id": page_id}))

    def save_instruments(self, project_id: str, page_id: str, instruments: list[dict]) -> list[dict]:
        return self._save_many("instruments", instruments, {"project_id": project_id, "page_id": page_id})

    def save_equipment(self, project_id: str, page_id: str, equipment: list[dict]) -> list[dict]:
        return self._save_many("equipment", equipment, {"project_id": project_id, "page_id": page_id})

    def save_lines(self, project_id: str, page_id: str, lines: list[dict]) -> list[dict]:
        return self._save_many("lines", lines, {"project_id": project_id, "page_id": page_id})

    def save_annotations(self, project_id: str, page_id: str, annotations: list[dict]) -> list[dict]:
        return self._save_many("annotations", annotations, {"project_id": project_id, "page_id": page_id})

    def save_relationships(self, project_id: str, page_id: str, relationships: list[dict]) -> list[dict]:
        return self._save_many("relationships", relationships, {"project_id": project_id, "page_id": page_id})

    def get_job_result(self, job_id: str) -> dict:
        job = self.db.extraction_jobs.find_one({"_id": job_id})
        if not job:
            raise NotFoundError(f"Job {job_id} not found")
        pages = list(self.db.pages.find({"job_id": job_id}).sort("page_number", 1))
        page_results = []
        all_relationships = []
        for page in pages:
            pid = page["_id"]
            rels = list(self.db.relationships.find({"page_id": pid}))
            all_relationships.extend(rels)
            page_results.append({
                "page_id": pid,
                "page_number": page["page_number"],
                "source_filename": page["source_filename"],
                "symbols": list(self.db.symbols.find({"page_id": pid})),
                "instruments": list(self.db.instruments.find({"page_id": pid})),
                "equipment": list(self.db.equipment.find({"page_id": pid})),
                "lines": list(self.db.lines.find({"page_id": pid})),
                "annotations": list(self.db.annotations.find({"page_id": pid})),
            })
        return {
            "job_id": job_id, "project_id": job["project_id"], "status": job["status"],
            "pages": page_results, "relationships": all_relationships,
        }

    # --- human-in-the-loop ---

    def save_unknown_symbol(self, job_id: str, page_id: str, page_number: int,
                             symbol_id: Optional[str], bbox: dict, crop_image_path: str,
                             surrounding_text: Optional[str], original_confidence: Optional[float]) -> dict:
        doc = {
            "_id": _uuid(), "job_id": job_id, "page_id": page_id, "symbol_id": symbol_id,
            "page_number": page_number, "bbox": bbox, "crop_image_path": crop_image_path,
            "surrounding_text": surrounding_text, "original_confidence": original_confidence,
            "status": "pending", "user_provided_category": None, "created_at": _now(),
        }
        self.db.unknown_symbols.insert_one(doc)
        return doc

    def get_unknown_symbol(self, unknown_symbol_id: str) -> Optional[dict]:
        return self.db.unknown_symbols.find_one({"_id": unknown_symbol_id})

    def get_pending_unknown_symbols(self, job_id: str) -> list[dict]:
        return list(self.db.unknown_symbols.find({"job_id": job_id, "status": "pending"}))

    def resolve_unknown_symbol(self, unknown_symbol_id: str, category_name: str, attributes: dict) -> dict:
        doc = self.db.unknown_symbols.find_one({"_id": unknown_symbol_id})
        if not doc:
            raise NotFoundError(f"Unknown symbol {unknown_symbol_id} not found")
        update = {
            "status": "labeled", "user_provided_category": category_name,
            "user_provided_attributes": attributes, "resolved_at": _now(),
        }
        self.db.unknown_symbols.update_one({"_id": unknown_symbol_id}, {"$set": update})
        if doc.get("symbol_id"):
            self.db.symbols.update_one(
                {"_id": doc["symbol_id"]},
                {"$set": {"class_name": category_name, "is_unknown": False,
                          "resolved_from_unknown_id": unknown_symbol_id}},
            )
        doc.update(update)
        return doc

    def add_symbol_dictionary_entry(self, category_name: str, source: str,
                                     isa_type_code: Optional[str] = None,
                                     description: Optional[str] = None,
                                     reference_crop_path: Optional[str] = None,
                                     shape_signature: Optional[Any] = None,
                                     attributes_schema: Optional[dict] = None) -> dict:
        existing = self.db.symbol_dictionary.find_one({"category_name": category_name})
        if existing:
            return existing
        doc = {
            "_id": _uuid(), "category_name": category_name, "source": source,
            "isa_type_code": isa_type_code, "description": description,
            "reference_crop_path": reference_crop_path, "shape_signature": shape_signature,
            "attributes_schema": attributes_schema or {}, "created_at": _now(),
        }
        self.db.symbol_dictionary.insert_one(doc)
        return doc

    def get_symbol_dictionary(self) -> list[dict]:
        return list(self.db.symbol_dictionary.find().sort("category_name", 1))

    def find_symbol_dictionary_by_signature(self, shape_signature: Any) -> Optional[dict]:
        from app.services.cv.symbol_signature import signatures_match
        for entry in self.db.symbol_dictionary.find({"source": "user_labeled"}):
            if entry.get("shape_signature") and signatures_match(entry["shape_signature"], shape_signature):
                return entry
        return None

    # --- queries ---

    def get_project_tags(self, project_id: str) -> list[dict]:
        tags = []
        for inst in self.db.instruments.find({"project_id": project_id, "tag": {"$ne": None}}):
            tags.append({"tag": inst["tag"], "entity_type": "instrument", "entity_id": inst["_id"], "page_id": inst["page_id"]})
        for eq in self.db.equipment.find({"project_id": project_id, "tag": {"$ne": None}}):
            tags.append({"tag": eq["tag"], "entity_type": "equipment", "entity_id": eq["_id"], "page_id": eq["page_id"]})
        for ln in self.db.lines.find({"project_id": project_id, "line_number": {"$ne": None}}):
            tags.append({"tag": ln["line_number"], "entity_type": "line", "entity_id": ln["_id"], "page_id": ln["page_id"]})
        return tags

    def find_duplicate_tags(self, project_id: Optional[str] = None) -> list[dict]:
        query: dict = {"project_id": project_id} if project_id else {}
        tag_map: dict[str, list[dict]] = {}
        for inst in self.db.instruments.find({**query, "tag": {"$ne": None}}):
            tag_map.setdefault(inst["tag"], []).append(
                {"entity_id": inst["_id"], "entity_type": "instrument", "page_id": inst["page_id"], "project_id": inst["project_id"]}
            )
        for eq in self.db.equipment.find({**query, "tag": {"$ne": None}}):
            tag_map.setdefault(eq["tag"], []).append(
                {"entity_id": eq["_id"], "entity_type": "equipment", "page_id": eq["page_id"], "project_id": eq["project_id"]}
            )
        return [{"tag": t, "occurrences": occ} for t, occ in tag_map.items() if len(occ) > 1]
