"""Explicit local demo changes; no external notifications or ingestion."""
from fastapi import APIRouter, Depends, HTTPException

from backend.agents.freshness import refresh_expired, simulate_change
from backend.contracts import ChangeEvent, ChangeRequest
from backend.db.repository import get_repo
from backend.security import optional_principal, require_hotelier

router = APIRouter(prefix="/api/changes", tags=["changes"])
_REPO = Depends(get_repo)
_AUTH = Depends(require_hotelier)
_OPTIONAL_AUTH = Depends(optional_principal)


def project_event(value, repo, principal):
    """Do not distribute guest read capabilities via the public change feed."""
    event = ChangeEvent.model_validate(value).model_copy(deep=True)
    event.payload = {**event.payload, "notification_count": len(event.notifications)}
    if principal is None:
        event.notifications = []
    elif principal.role != "demo_admin":
        visible = []
        for notification in event.notifications:
            passport = repo.get("expectation_passports", notification.get("passport_id", ""))
            if passport and principal.permits(passport["hotel_id"]):
                visible.append(notification)
        event.notifications = visible
    return event


def require_demo_admin(principal):
    if principal.role != "demo_admin":
        raise HTTPException(403, "Глобальное изменение доступно только demo admin")


@router.get("", response_model=list[ChangeEvent])
def changes(repo=_REPO, _user=_OPTIONAL_AUTH):
    return [project_event(row, repo, _user) for row in
            sorted(repo.all("change_events"), key=lambda row: row.get("detected_at", ""), reverse=True)]


@router.post("/simulate", response_model=ChangeEvent)
def simulate(body: ChangeRequest, repo=_REPO, _user=_AUTH):
    require_demo_admin(_user)
    try:
        return project_event(simulate_change(repo, body.hotel_id), repo, _user)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except (ValueError, OSError) as error:
        raise HTTPException(409, "Пересчёт недоступен: " + str(error)[:300]) from error


@router.post("/refresh")
def refresh(repo=_REPO, _user=_AUTH):
    require_demo_admin(_user)
    try:
        return refresh_expired(repo)
    except (ValueError, OSError) as error:
        raise HTTPException(409, "Проверка недоступна: " + str(error)[:300]) from error
