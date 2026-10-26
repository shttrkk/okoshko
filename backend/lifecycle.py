"""Recover interrupted local jobs and recheck expired observations periodically."""
import hashlib
import logging
import threading
from datetime import UTC, datetime

from backend.contracts import now


def recover_interrupted(repo):
    count = 0
    for table in ("agent_runs", "jobs"):
        for row in repo.find(table, status="running"):
            repo.upsert(table, {**row, "status": "failed", "finished_at": now().isoformat(),
                                "error": "Прервано перезапуском приложения; запустите повторно"})
            count += 1
    return count


def check_expiry(repo):
    token = repo.expiry_token()
    if not token:
        return
    # One local attempt per unchanged expired set/day; an old archive is not
    # silently relabelled as a fresh measurement on every timer tick.
    identity = "freshness-" + hashlib.sha256((token + datetime.now(UTC).date().isoformat()).encode()).hexdigest()[:24]
    existing = repo.get("jobs", identity)
    if existing and existing.get("status") in {"running", "completed"}:
        return
    job = {"id": identity, "kind": "freshness_watch", "status": "running", "started_at": now().isoformat()}
    repo.upsert("jobs", job)
    from backend.agents.freshness import _LOCK, refresh_expired
    try:
        with _LOCK:
            result = refresh_expired(repo)
        repo.upsert("jobs", {**job, "status": "completed", "result": result, "finished_at": now().isoformat()})
    except Exception as error:
        repo.upsert("jobs", {**job, "status": "failed", "error": str(error)[:300], "finished_at": now().isoformat()})
        raise


def start_watcher(repo, interval_s=60):
    stop = threading.Event()

    def watch():
        while not stop.wait(interval_s):
            try:
                check_expiry(repo)
            except Exception:
                logging.exception("Local freshness recheck failed; facts retain their original validity")

    thread = threading.Thread(target=watch, name="freshness-watcher", daemon=True)
    thread.start()
    return stop
