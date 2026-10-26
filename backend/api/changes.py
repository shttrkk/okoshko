"""Explicit local demo changes; no external notifications or ingestion."""
from fastapi import APIRouter, Depends, HTTPException

from backend.agents.freshness import refresh_expired, simulate_change
from backend.contracts import ChangeEvent, ChangeRequest
from backend.db.repository import get_repo

router = APIRouter(prefix="/api/changes", tags=["changes"])
_REPO = Depends(get_repo)


@router.get("", response_model=list[ChangeEvent])
def changes(repo=_REPO):
    return sorted(repo.all("change_events"), key=lambda row: row.get("detected_at", ""), reverse=True)


@router.post("/simulate", response_model=ChangeEvent)
def simulate(body: ChangeRequest, repo=_REPO):
    try:
        return simulate_change(repo, body.hotel_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except (ValueError, OSError) as error:
        raise HTTPException(409, "Пересчёт недоступен: " + str(error)[:300]) from error


@router.post("/refresh")
def refresh(repo=_REPO):
    try:
        return refresh_expired(repo)
    except (ValueError, OSError) as error:
        raise HTTPException(409, "Проверка недоступна: " + str(error)[:300]) from error
