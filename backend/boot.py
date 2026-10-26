import logging
import os

import uvicorn
from alembic import command
from alembic.config import Config

from backend.settings import ROOT

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def migrate():
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    logging.info("Database migrations complete")


def main():
    migrate()
    # Stream S3 implements COPY seed ingestion and spec loading here.
    logging.info("О!кно foundation ready, LLM_MODE=%s", os.getenv("LLM_MODE", "offline"))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8080, access_log=True)


if __name__ == "__main__":
    main()

