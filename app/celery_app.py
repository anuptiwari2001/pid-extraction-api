"""
Optional Celery worker path. Only used when USE_CELERY=true — otherwise
POST /extract runs jobs via FastAPI's BackgroundTasks, which is simpler to
run locally/demo but doesn't survive an API process restart or scale past
one worker. Switch to Celery for production multi-worker deployments (see
docker-compose.yml, which brings up a `worker` service running this).

Run with: celery -A app.celery_app worker --loglevel=info
"""
from celery import Celery

from app.core.config import get_settings
from app.core.logging_config import configure_logging, get_logger
from app.db.factory import get_repository

settings = get_settings()
configure_logging()
logger = get_logger(__name__)

celery_app = Celery(
    "pid_extraction",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]


@celery_app.task(name="run_extraction_job_task")
def run_extraction_job_task(job_id: str, pdf_paths: list[str]) -> None:
    from app.services.extraction.job_runner import run_extraction_job
    repo = get_repository()
    run_extraction_job(repo, job_id, pdf_paths)


@celery_app.task(name="resume_job_task")
def resume_job_task(job_id: str) -> None:
    from app.services.extraction.job_runner import resume_job_if_unblocked
    repo = get_repository()
    resume_job_if_unblocked(repo, job_id)
