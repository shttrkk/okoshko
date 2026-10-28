"""Offline studio: Russian proximity templates compile to validated declarative specs."""
import pytest
from fastapi.testclient import TestClient

from backend.agents.spec_compiler import validate_spec
from backend.agents.spec_templates import CATEGORY_WORDS, SUPPORTED, compile_template
from backend.db.repository import get_repo
from backend.main import app
from backend.tests.test_api_integration import repo  # noqa: F401  (fixture)
from backend.tools.registry import POI_CATEGORIES, Registry

CASES = [
    ("супермаркет рядом", "supermarket", 500), ("продукты в 5 минутах", "supermarket", 400), ("аптека ближе 300 м", "pharmacy", 300),
    ("кафе до 200 метров", "cafe", 200), ("ресторан рядом", "restaurant", 500), ("бар не дальше 400 м", "bar", 400),
    ("остановка в 3 минутах", "bus_stop", 240), ("парк в пределах 1 км", "park", 1000), ("банкомат рядом", "atm", 500),
    ("детская площадка до 300 м", "playground", 300), ("спортзал в 10 минутах", "fitness", 800), ("рынок рядом", "marketplace", 500),
    ("пляж в 5 минутах", "beach", 400), ("набережная рядом", "promenade", 500), ("прокат велосипедов рядом", "rental", 500),
    ("заправка до 2 км", "fuel", 2000),
]


@pytest.mark.parametrize(("text", "category", "metres"), CASES)
def test_proximity_templates_cover_the_registry_categories(text, category, metres):
    spec = compile_template(text)
    assert spec.id == f"near_{category}_{metres}m" and spec.methods[0].tool == "poi_nearby"
    assert spec.methods[0].params == {"category": category, "near_m": metres}
    assert (spec.search.field, spec.search.default_op, spec.search.default_value, spec.search.unit) == ("distance_m", "lte", metres, "м")
    validate_spec(spec, Registry(None))


def test_every_registry_category_has_a_russian_template():
    assert {category for _, category in CATEGORY_WORDS} == set(POI_CATEGORIES)


def test_the_composite_morning_swim_example_still_compiles():
    spec = compile_template("Искупаться до завтрака: до 7 минут до пляжа, вода от 22°")
    assert spec.id == "morning_swim" and spec.methods[0].params["expr"] == "beach_walk.minutes <= 7 and climate.water_c >= 22"


def test_studio_compiles_and_previews_a_template_and_explains_unknown_text(repo):  # noqa: F811
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        with TestClient(app) as client:
            compiled = client.post("/api/characteristics/compile", json={"text": "супермаркет рядом"})
            preview = client.post("/api/characteristics/dry-run", json=compiled.json()["spec"])
            unknown = client.post("/api/characteristics/compile", json={"text": "вайб как в Барселоне"})
    finally:
        app.dependency_overrides.clear()
    assert compiled.status_code == 200 and compiled.json()["spec"]["id"] == "near_supermarket_500m"
    assert compiled.json()["mode"] == "offline" and "category: supermarket" in compiled.json()["yaml_text"]
    assert preview.status_code == 200 and preview.json()["valid"]
    claims = [claim for row in preview.json()["results"] for claim in row["claims"]]
    assert claims and all(claim["method"] == "local_poi_category@1" and "distance_m" in claim["value"] for claim in claims)
    assert unknown.status_code == 422 and SUPPORTED[:40] in unknown.json()["detail"]


@pytest.mark.parametrize(("text", "category", "metres"), [
    ("рядом нет ночных клубов", "bar", 500), ("без баров ближе 300 м", "bar", 300),
    ("подальше от рынка", "marketplace", 500), ("чтобы не было заправки в пределах 1 км", "fuel", 1000),
])
def test_absence_of_a_place_is_not_turned_into_its_presence(text, category, metres):
    spec = compile_template(text)
    assert spec.id == f"no_{category}_{metres}m" and spec.methods[0].params == {"category": category, "near_m": metres}
    assert (spec.search.field, spec.search.default_op, spec.search.default_value) == ("available", "eq", False)
    validate_spec(spec, Registry(None))


def test_limits_with_ne_stay_proximity_conditions():
    assert compile_template("бар не дальше 400 м").id == "near_bar_400m"
    assert compile_template("нет шумных баров рядом").id == "no_bar_500m"
    assert compile_template("пляж в 5 минутах без лестниц").id == "near_beach_400m"
    assert compile_template("без лестниц до пляжа рядом").id == "near_beach_500m"


def test_a_word_containing_a_synonym_is_not_that_place(repo):  # noqa: F811
    from backend.agents.spec_compiler import compile_spec
    with pytest.raises(ValueError, match="не сопоставлено"):
        compile_spec("аквапарк рядом", None, repo, Registry(repo))
