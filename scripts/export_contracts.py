"""Regenerate OpenAPI and JSON Schema from the shared models."""
import json
from pathlib import Path

from backend.contracts import models
from backend.main import app

root = Path(__file__).resolve().parents[1]
destination = root / "docs"
(destination / "openapi.json").write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2))
schemas = {
    name: cls.model_json_schema()
    for name, cls in vars(models).items()
    if isinstance(cls, type) and issubclass(cls, models.Model) and cls is not models.Model
}
(destination / "schemas.json").write_text(json.dumps(schemas, ensure_ascii=False, indent=2))
print(f"Exported OpenAPI and {len(schemas)} schemas")

