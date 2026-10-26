"""Same-origin API and SPA. Startup never downloads data or invokes an LLM."""
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.api.catalog import router as catalog_router
from backend.api.catalog import runtime_dir
from backend.api.hotelier import router as hotelier_router
from backend.api.passports import router as passports_router
from backend.api.runs import router as runs_router
from backend.db.repository import get_repo
from backend.settings import DATA, STATIC

app = FastAPI(title="О!кно API", version="1.0.0", docs_url="/api/docs")
app.include_router(catalog_router)
app.include_router(hotelier_router)
app.include_router(passports_router)
app.include_router(runs_router)
_REPO = Depends(get_repo)


@app.get("/health")
def health(repo=_REPO) -> dict[str, str]:
    try:
        if not repo.healthy() or not repo.all("hotels") or not repo.all("characteristics"):
            raise HTTPException(503, "База данных и seed ещё не готовы")
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, "База данных и seed ещё не готовы") from error
    return {"status": "ok"}


# Explicit public directories: simulator profiles, prompts and caches stay private.
for directory in ("seed", "geo", "tiles", "eval", "fixtures"):
    if (DATA / directory).is_dir():
        app.mount("/data/" + directory, StaticFiles(directory=DATA / directory), name="data-" + directory)
app.mount("/data/runtime", StaticFiles(directory=runtime_dir(), check_dir=False), name="runtime")
if (STATIC / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith(("api/", "data/")):
        raise HTTPException(404)
    target = (STATIC / path).resolve()
    if target.is_relative_to(STATIC.resolve()) and target.is_file():
        return FileResponse(target)
    if (STATIC / "index.html").is_file():
        return FileResponse(STATIC / "index.html")
    return HTMLResponse("<html lang='ru'><title>О!кно</title><h1>О!кно</h1><p>Демо-регион: Сочи</p></html>")
