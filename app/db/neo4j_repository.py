"""
Neo4j repository. Requires the `neo4j` driver. This is the "graph-native"
backend — projects/pages/jobs are simple property nodes, but symbols,
instruments, equipment, and lines are nodes connected by real graph edges
(CONNECTED_TO / CONTROLS / MEASURES / BELONGS_TO), so relationship queries
(e.g. "everything downstream of pump P-101") are native Cypher traversals
instead of app-side joins.

Note: unknown-symbol / symbol-dictionary bookkeeping and job metadata are
kept as plain nodes too, for API contract parity with the SQL/Mongo backends
— Neo4j's real advantage here is the `relationships` graph itself.
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Neo4jRepository(BaseRepository):
    def __init__(self, uri: str, user: str, password: str):
        self.uri = uri
        self.user = user
        self.password = password
        self.driver = None

    def connect(self) -> None:
        try:
            from neo4j import GraphDatabase
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            self.driver.verify_connectivity()
            logger.info("db_connected", extra={"context": {"dialect": "neo4j"}})
        except Exception as exc:
            raise DatabaseConnectionError("Failed to connect to Neo4j", {"error": str(exc)})

    def test_connection(self) -> bool:
        try:
            if self.driver is None:
                self.connect()
            self.driver.verify_connectivity()
            return True
        except Exception:
            return False

    def init_schema(self) -> None:
        if self.driver is None:
            self.connect()
        constraints = [
            "CREATE CONSTRAINT project_id IF NOT EXISTS FOR (n:Project) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT job_id IF NOT EXISTS FOR (n:ExtractionJob) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT page_id IF NOT EXISTS FOR (n:Page) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT symbol_id IF NOT EXISTS FOR (n:Symbol) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT instrument_id IF NOT EXISTS FOR (n:Instrument) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT equipment_id IF NOT EXISTS FOR (n:Equipment) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT line_id IF NOT EXISTS FOR (n:Line) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT unknown_symbol_id IF NOT EXISTS FOR (n:UnknownSymbol) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT symbol_dict_category IF NOT EXISTS FOR (n:SymbolDictionaryEntry) REQUIRE n.category_name IS UNIQUE",
        ]
        with self.driver.session() as session:
            for c in constraints:
                session.run(c)
        logger.info("schema_initialized", extra={"context": {"dialect": "neo4j"}})

    def _run(self, query: str, **params) -> list[dict]:
        with self.driver.session() as session:
            result = session.run(query, **params)
            return [dict(r) for r in result]

    # --- projects & jobs ---

    def create_project(self, name: str, description: Optional[str] = None) -> dict:
        pid = _uuid()
        rows = self._run(
            "CREATE (p:Project {id: $id, name: $name, description: $description, created_at: $created_at}) "
            "RETURN p", id=pid, name=name, description=description, created_at=_now_iso(),
        )
        return dict(rows[0]["p"])

    def get_project(self, project_id: str) -> Optional[dict]:
        rows = self._run("MATCH (p:Project {id: $id}) RETURN p", id=project_id)
        return dict(rows[0]["p"]) if rows else None

    def create_extraction_job(self, project_id: str, source_filenames: list[str],
                               confidence_threshold: float, auto_learn_unknowns: bool) -> dict:
        jid = _uuid()
        rows = self._run(
            "MATCH (p:Project {id: $project_id}) "
            "CREATE (j:ExtractionJob {id: $id, project_id: $project_id, status: 'queued', progress_pct: 0.0, "
            "confidence_threshold: $threshold, auto_learn_unknowns: $auto_learn, "
            "source_filenames: $filenames, created_at: $now, updated_at: $now})-[:BELONGS_TO_PROJECT]->(p) "
            "RETURN j",
            id=jid, project_id=project_id, threshold=confidence_threshold,
            auto_learn=auto_learn_unknowns, filenames=source_filenames, now=_now_iso(),
        )
        return dict(rows[0]["j"])

    def update_job_status(self, job_id: str, status: str, progress_pct: Optional[float] = None,
                           error_message: Optional[str] = None) -> None:
        rows = self._run(
            "MATCH (j:ExtractionJob {id: $id}) "
            "SET j.status = $status, j.updated_at = $now"
            + (", j.progress_pct = $progress" if progress_pct is not None else "")
            + (", j.error_message = $error" if error_message is not None else "")
            + " RETURN j",
            id=job_id, status=status, now=_now_iso(), progress=progress_pct, error=error_message,
        )
        if not rows:
            raise NotFoundError(f"Job {job_id} not found")

    def get_job(self, job_id: str) -> Optional[dict]:
        rows = self._run("MATCH (j:ExtractionJob {id: $id}) RETURN j", id=job_id)
        return dict(rows[0]["j"]) if rows else None

    # --- pages ---

    def create_page(self, job_id: str, project_id: str, page_number: int, source_filename: str,
                     image_path: str, width_px: int, height_px: int) -> dict:
        page_id = _uuid()
        rows = self._run(
            "MATCH (j:ExtractionJob {id: $job_id}) "
            "CREATE (pg:Page {id: $id, job_id: $job_id, project_id: $project_id, page_number: $page_number, "
            "source_filename: $filename, image_path: $image_path, width_px: $w, height_px: $h, status: 'pending'"
            "})-[:PAGE_OF]->(j) RETURN pg",
            id=page_id, job_id=job_id, project_id=project_id, page_number=page_number,
            filename=source_filename, image_path=image_path, w=width_px, h=height_px,
        )
        return dict(rows[0]["pg"])

    def update_page_status(self, page_id: str, status: str) -> None:
        rows = self._run("MATCH (pg:Page {id: $id}) SET pg.status = $status RETURN pg", id=page_id, status=status)
        if not rows:
            raise NotFoundError(f"Page {page_id} not found")

    def get_pages_for_job(self, job_id: str) -> list[dict]:
        rows = self._run(
            "MATCH (pg:Page {job_id: $job_id}) RETURN pg ORDER BY pg.page_number", job_id=job_id
        )
        return [dict(r["pg"]) for r in rows]

    # --- extraction results ---
    # Entities are created as real nodes; source/target linkage for
    # relationships is done via MERGE in save_relationships so the graph is
    # actually connected (this is the whole point of choosing Neo4j).

    def _create_nodes(self, label: str, page_id: str, project_id: Optional[str], items: list[dict]) -> list[dict]:
        created = []
        for item in items:
            node_id = _uuid()
            props = {"id": node_id, "page_id": page_id, **item}
            if project_id:
                props["project_id"] = project_id
            # Flatten bbox if present
            if "bbox" in props and isinstance(props["bbox"], dict):
                bbox = props.pop("bbox")
                props.update({f"bbox_{k}": v for k, v in bbox.items()})
            rows = self._run(
                f"MATCH (pg:Page {{id: $page_id}}) "
                f"CREATE (n:{label} $props)-[:ON_PAGE]->(pg) RETURN n",
                page_id=page_id, props=props,
            )
            created.append(dict(rows[0]["n"]))
        return created

    def save_symbols(self, page_id: str, symbols: list[dict]) -> list[dict]:
        return self._create_nodes("Symbol", page_id, None, symbols)

    def get_symbols_for_page(self, page_id: str) -> list[dict]:
        rows = self._run("MATCH (n:Symbol {page_id: $pid}) RETURN n", pid=page_id)
        return [dict(r["n"]) for r in rows]

    def save_instruments(self, project_id: str, page_id: str, instruments: list[dict]) -> list[dict]:
        return self._create_nodes("Instrument", page_id, project_id, instruments)

    def save_equipment(self, project_id: str, page_id: str, equipment: list[dict]) -> list[dict]:
        return self._create_nodes("Equipment", page_id, project_id, equipment)

    def save_lines(self, project_id: str, page_id: str, lines: list[dict]) -> list[dict]:
        return self._create_nodes("Line", page_id, project_id, lines)

    def save_annotations(self, project_id: str, page_id: str, annotations: list[dict]) -> list[dict]:
        return self._create_nodes("Annotation", page_id, project_id, annotations)

    def save_relationships(self, project_id: str, page_id: str, relationships: list[dict]) -> list[dict]:
        created = []
        for rel in relationships:
            rel_type = rel["relation_type"].upper()
            rows = self._run(
                "MATCH (src {id: $src_id}), (tgt {id: $tgt_id}) "
                f"MERGE (src)-[r:{rel_type} {{confidence: $confidence, inferred_by: $inferred_by, "
                "project_id: $project_id, page_id: $page_id}}]->(tgt) RETURN r",
                src_id=rel["source_entity_id"], tgt_id=rel["target_entity_id"],
                confidence=rel.get("confidence", 1.0), inferred_by=rel.get("inferred_by", "rule_based"),
                project_id=project_id, page_id=page_id,
            )
            if rows:
                created.append({**dict(rows[0]["r"]), "relation_type": rel["relation_type"],
                                 "source_entity_id": rel["source_entity_id"], "target_entity_id": rel["target_entity_id"]})
        return created

    def get_job_result(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        if not job:
            raise NotFoundError(f"Job {job_id} not found")
        pages = self.get_pages_for_job(job_id)
        page_results = []
        all_relationships = []
        for page in pages:
            pid = page["id"]
            symbols = [dict(r["n"]) for r in self._run("MATCH (n:Symbol {page_id: $pid}) RETURN n", pid=pid)]
            instruments = [dict(r["n"]) for r in self._run("MATCH (n:Instrument {page_id: $pid}) RETURN n", pid=pid)]
            equipment = [dict(r["n"]) for r in self._run("MATCH (n:Equipment {page_id: $pid}) RETURN n", pid=pid)]
            lines = [dict(r["n"]) for r in self._run("MATCH (n:Line {page_id: $pid}) RETURN n", pid=pid)]
            annotations = [dict(r["n"]) for r in self._run("MATCH (n:Annotation {page_id: $pid}) RETURN n", pid=pid)]
            rels = self._run(
                "MATCH (src)-[r]->(tgt) WHERE r.page_id = $pid "
                "RETURN src.id AS source_entity_id, tgt.id AS target_entity_id, type(r) AS relation_type, "
                "r.confidence AS confidence, r.inferred_by AS inferred_by", pid=pid,
            )
            all_relationships.extend(rels)
            page_results.append({
                "page_id": pid, "page_number": page["page_number"], "source_filename": page["source_filename"],
                "symbols": symbols, "instruments": instruments, "equipment": equipment,
                "lines": lines, "annotations": annotations,
            })
        return {"job_id": job_id, "project_id": job["project_id"], "status": job["status"],
                "pages": page_results, "relationships": all_relationships}

    # --- human-in-the-loop ---

    def save_unknown_symbol(self, job_id: str, page_id: str, page_number: int,
                             symbol_id: Optional[str], bbox: dict, crop_image_path: str,
                             surrounding_text: Optional[str], original_confidence: Optional[float]) -> dict:
        uid = _uuid()
        rows = self._run(
            "CREATE (u:UnknownSymbol {id: $id, job_id: $job_id, page_id: $page_id, symbol_id: $symbol_id, "
            "page_number: $page_number, bbox_x1: $x1, bbox_y1: $y1, bbox_x2: $x2, bbox_y2: $y2, "
            "crop_image_path: $crop_path, surrounding_text: $context, original_confidence: $conf, "
            "status: 'pending', created_at: $now}) RETURN u",
            id=uid, job_id=job_id, page_id=page_id, symbol_id=symbol_id, page_number=page_number,
            x1=bbox["x1"], y1=bbox["y1"], x2=bbox["x2"], y2=bbox["y2"],
            crop_path=crop_image_path, context=surrounding_text, conf=original_confidence, now=_now_iso(),
        )
        return dict(rows[0]["u"])

    def get_unknown_symbol(self, unknown_symbol_id: str) -> Optional[dict]:
        rows = self._run("MATCH (u:UnknownSymbol {id: $id}) RETURN u", id=unknown_symbol_id)
        return dict(rows[0]["u"]) if rows else None

    def get_pending_unknown_symbols(self, job_id: str) -> list[dict]:
        rows = self._run(
            "MATCH (u:UnknownSymbol {job_id: $job_id, status: 'pending'}) RETURN u", job_id=job_id
        )
        return [dict(r["u"]) for r in rows]

    def resolve_unknown_symbol(self, unknown_symbol_id: str, category_name: str, attributes: dict) -> dict:
        rows = self._run(
            "MATCH (u:UnknownSymbol {id: $id}) "
            "SET u.status = 'labeled', u.user_provided_category = $category, "
            "u.user_provided_attributes = $attrs, u.resolved_at = $now "
            "WITH u OPTIONAL MATCH (s:Symbol {id: u.symbol_id}) "
            "SET s.class_name = CASE WHEN s IS NOT NULL THEN $category ELSE s.class_name END, "
            "s.is_unknown = CASE WHEN s IS NOT NULL THEN false ELSE s.is_unknown END "
            "RETURN u",
            id=unknown_symbol_id, category=category_name, attrs=attributes, now=_now_iso(),
        )
        if not rows:
            raise NotFoundError(f"Unknown symbol {unknown_symbol_id} not found")
        return dict(rows[0]["u"])

    def add_symbol_dictionary_entry(self, category_name: str, source: str,
                                     isa_type_code: Optional[str] = None,
                                     description: Optional[str] = None,
                                     reference_crop_path: Optional[str] = None,
                                     shape_signature: Optional[Any] = None,
                                     attributes_schema: Optional[dict] = None) -> dict:
        rows = self._run(
            "MERGE (e:SymbolDictionaryEntry {category_name: $category}) "
            "ON CREATE SET e.id = $id, e.source = $source, e.isa_type_code = $isa_code, "
            "e.description = $description, e.reference_crop_path = $crop_path, "
            "e.created_at = $now "
            "RETURN e",
            category=category_name, id=_uuid(), source=source, isa_code=isa_type_code,
            description=description, crop_path=reference_crop_path, now=_now_iso(),
        )
        return dict(rows[0]["e"])

    def get_symbol_dictionary(self) -> list[dict]:
        rows = self._run("MATCH (e:SymbolDictionaryEntry) RETURN e ORDER BY e.category_name")
        return [dict(r["e"]) for r in rows]

    def find_symbol_dictionary_by_signature(self, shape_signature: Any) -> Optional[dict]:
        # Neo4j has no native fuzzy shape matching; pull user-labeled entries
        # and reuse the same Python-side comparator as the other backends.
        from app.services.cv.symbol_signature import signatures_match
        rows = self._run("MATCH (e:SymbolDictionaryEntry {source: 'user_labeled'}) RETURN e")
        for r in rows:
            entry = dict(r["e"])
            if entry.get("shape_signature") and signatures_match(entry["shape_signature"], shape_signature):
                return entry
        return None

    # --- queries ---

    def get_project_tags(self, project_id: str) -> list[dict]:
        rows = self._run(
            "MATCH (n) WHERE n.project_id = $pid AND (n:Instrument OR n:Equipment OR n:Line) "
            "AND (n.tag IS NOT NULL OR n.line_number IS NOT NULL) "
            "RETURN coalesce(n.tag, n.line_number) AS tag, labels(n)[0] AS entity_type, n.id AS entity_id, n.page_id AS page_id",
            pid=project_id,
        )
        return rows

    def find_duplicate_tags(self, project_id: Optional[str] = None) -> list[dict]:
        where = "n.project_id = $pid AND" if project_id else ""
        rows = self._run(
            f"MATCH (n) WHERE {where} (n:Instrument OR n:Equipment) AND n.tag IS NOT NULL "
            "WITH n.tag AS tag, collect({entity_id: n.id, entity_type: labels(n)[0], page_id: n.page_id, project_id: n.project_id}) AS occurrences "
            "WHERE size(occurrences) > 1 RETURN tag, occurrences",
            pid=project_id,
        )
        return rows
