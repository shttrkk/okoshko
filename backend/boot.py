import logging
import os

import uvicorn
from alembic import command
from alembic.config import Config

from backend.db.repository import get_repo
from backend.lifecycle import recover_interrupted, start_watcher
from backend.seed import load_seed
from backend.settings import ROOT

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def migrate():
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    logging.info("Database migrations complete")


def main():
    migrate()
    logging.info("Recovered interrupted jobs/runs: %s", recover_interrupted(get_repo()))
    load_seed()
    get_repo().search_facts()
    start_watcher(get_repo())
    logging.info("О!кно ready; local search snapshot primed, LLM_MODE=%s", os.getenv("LLM_MODE", "offline"))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8080, access_log=True)


if __name__ == "__main__":
    main()

