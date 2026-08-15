from app.db.base_repository import BaseRepository
from app.db.factory import get_repository


def get_repo() -> BaseRepository:
    return get_repository()
