from unittest.mock import patch

from backend.lifecycle import check_expiry, recover_interrupted
from backend.tests.test_hotelier import MemoryRepo


def test_restart_fails_interrupted_work_without_claiming_success():
    repo = MemoryRepo()
    repo.insert("agent_runs", {"id": "interrupted", "status": "running"})
    repo.insert("jobs", {"id": "done", "status": "completed"})
    assert recover_interrupted(repo) == 1
    assert repo.get("agent_runs", "interrupted")["status"] == "failed"
    assert repo.get("jobs", "done")["status"] == "completed"


def test_same_expired_snapshot_rechecked_once_without_faking_freshness():
    repo = MemoryRepo()
    repo.expiry_token = lambda: "unchanged-old-observation"
    with patch("backend.agents.freshness.refresh_expired", return_value={"processed": 1}) as refresh:
        check_expiry(repo)
        check_expiry(repo)
        assert refresh.call_count == 1
    assert repo.all("jobs")[0]["status"] == "completed"
