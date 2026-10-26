"""Foundation HTTP shell; stream S3 replaces the API fixture router."""

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.contracts import (
    Answer,
    AnswerRequest,
    AskRequest,
    ChangeEvent,
    ChangeRequest,
    CharacteristicSpec,
    CompileRequest,
    ExpectationPassport,
    Hotel,
    HotelDetail,
    LabelRequest,
    LoginRequest,
    PassportAction,
    PassportCreate,
    Question,
    RunRequest,
    SearchRequest,
    SearchResult,
)
from backend.settings import DATA, STATIC

app = FastAPI(title="О!кно API", version="1.0.0", docs_url="/api/docs")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
def status() -> dict:
    return {"name": "О!кно", "mode": "offline", "phase": "foundation", "version": "1.0.0"}


@app.get("/api/hotels", response_model=list[Hotel])
def hotels():
    return []


def pending():
    raise HTTPException(501, "Ф0: маршрут зафиксирован контрактом, реализация в потоке S3")


@app.post("/api/search", response_model=SearchResult)
def search(body: SearchRequest):
    return pending()


@app.get("/api/hotels/{hotel_id}", response_model=HotelDetail)
def hotel(hotel_id: str):
    return pending()


@app.get("/api/hotels/{hotel_id}/geo")
def geo(hotel_id: str):
    return pending()


@app.get("/api/compare")
def compare(ids: str):
    return pending()


@app.post("/api/hotelier/login")
def login(body: LoginRequest):
    return pending()


@app.get("/api/hotelier/{hotel_id}")
def cabinet(hotel_id: str):
    return pending()


@app.post("/api/questions", response_model=Question)
def ask(body: AskRequest):
    return pending()


@app.post("/api/questions/{question_id}/answer", response_model=Answer)
def answer(question_id: str, body: AnswerRequest):
    return pending()


@app.post("/api/questions/{question_id}/simulate", response_model=Answer)
def simulate(question_id: str):
    return pending()


@app.post("/api/answers/{answer_id}/confirm", response_model=Answer)
def confirm(answer_id: str):
    return pending()


@app.post("/api/passports", response_model=ExpectationPassport)
def create_passport(body: PassportCreate):
    return pending()


@app.get("/api/passports", response_model=list[ExpectationPassport])
def passports(hotel_id: str | None = None):
    return pending()


@app.get("/api/passports/{passport_id}", response_model=ExpectationPassport)
def passport(passport_id: str):
    return pending()


@app.post("/api/passports/{passport_id}/action", response_model=ExpectationPassport)
def passport_action(passport_id: str, body: PassportAction):
    return pending()


@app.get("/api/characteristics", response_model=list[CharacteristicSpec])
def characteristics():
    return []


@app.post("/api/characteristics/compile")
def compile_spec(body: CompileRequest):
    return pending()


@app.post("/api/characteristics/dry-run")
def dry_run(body: CharacteristicSpec):
    return pending()


@app.post("/api/characteristics/publish")
def publish(body: CharacteristicSpec):
    return pending()


@app.get("/api/runs")
def runs():
    return []


@app.post("/api/runs")
def run(body: RunRequest):
    return pending()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    return pending()


@app.get("/api/runs/{run_id}/stream")
def stream(run_id: str):
    return pending()


@app.get("/api/changes", response_model=list[ChangeEvent])
def changes():
    return []


@app.post("/api/changes/simulate", response_model=ChangeEvent)
def change(body: ChangeRequest):
    return pending()


@app.post("/api/changes/refresh")
def refresh():
    return pending()


@app.get("/api/sources")
def sources():
    return []


@app.get("/api/quality")
def quality():
    return {"status": "pending", "label_count": 0, "metrics": None}


@app.get("/api/quality/label")
def label_queue():
    return []


@app.post("/api/quality/label")
def label(body: LabelRequest):
    return pending()


@app.get("/api/tools")
def tools():
    return []


if DATA.exists():
    app.mount("/data", StaticFiles(directory=DATA), name="data")
if (STATIC / "assets").exists():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    target = (STATIC / path).resolve()
    if target.is_relative_to(STATIC.resolve()) and target.is_file():
        return FileResponse(target)
    if (STATIC / "index.html").exists():
        return FileResponse(STATIC / "index.html")
    return HTMLResponse("<html lang='ru'><title>О!кно</title><h1>О!кно</h1><p>Фундамент готов.</p></html>")

