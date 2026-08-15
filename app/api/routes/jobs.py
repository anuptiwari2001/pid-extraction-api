from fastapi import APIRouter, Depends

from app.core.errors import NotFoundError
from app.db.base_repository import BaseRepository
from app.api.deps import get_repo
from app.schemas.schemas import JobStatusResponse, JobResultResponse, GraphExport, GraphNode, GraphEdge

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str, repo: BaseRepository = Depends(get_repo)):
    job = repo.get_job(job_id)
    if not job:
        raise NotFoundError(f"Job {job_id} not found")
    pending = repo.get_pending_unknown_symbols(job_id)
    return JobStatusResponse(
        job_id=job["id"], status=job["status"], progress_pct=job["progress_pct"],
        error_message=job.get("error_message"), pending_unknown_symbols=len(pending),
        updated_at=job["updated_at"],
    )


def _build_graph_export(result: dict) -> GraphExport:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    for page in result["pages"]:
        for inst in page["instruments"]:
            nodes.append(GraphNode(id=inst["id"], type="instrument", label=inst.get("tag") or inst["id"],
                                    attributes={"isa_type_code": inst.get("isa_type_code")}))
        for eq in page["equipment"]:
            nodes.append(GraphNode(id=eq["id"], type="equipment", label=eq.get("tag") or eq["id"],
                                    attributes={"equipment_type": eq.get("equipment_type")}))
        for ln in page["lines"]:
            nodes.append(GraphNode(id=ln["id"], type="line", label=ln.get("line_number") or ln["id"],
                                    attributes={"line_type": ln.get("line_type")}))
    for rel in result["relationships"]:
        edges.append(GraphEdge(
            source=rel["source_entity_id"], target=rel["target_entity_id"],
            relation_type=rel["relation_type"], confidence=rel.get("confidence", 1.0),
        ))
    return GraphExport(nodes=nodes, edges=edges)


@router.get("/jobs/{job_id}/result", response_model=JobResultResponse)
def get_job_result(job_id: str, repo: BaseRepository = Depends(get_repo)):
    result = repo.get_job_result(job_id)  # raises NotFoundError if missing
    _normalize_bboxes(result)
    graph = _build_graph_export(result)
    return JobResultResponse(
        job_id=result["job_id"], project_id=result["project_id"], status=result["status"],
        pages=result["pages"], relationships=result["relationships"], graph=graph,
    )


def _normalize_bboxes(result: dict) -> None:
    """
    Repository implementations store bounding boxes as flat bbox_x1/y1/x2/y2
    columns (SQL) or as whatever shape they were saved with (Mongo/Neo4j).
    The API contract (SymbolOut.bbox) expects a nested {x1,y1,x2,y2} object,
    so normalize here rather than forcing every repository to match the
    Pydantic shape internally.
    """
    for page in result["pages"]:
        for sym in page.get("symbols", []):
            if "bbox" not in sym or sym["bbox"] is None:
                sym["bbox"] = {
                    "x1": sym.pop("bbox_x1", 0.0), "y1": sym.pop("bbox_y1", 0.0),
                    "x2": sym.pop("bbox_x2", 0.0), "y2": sym.pop("bbox_y2", 0.0),
                }
