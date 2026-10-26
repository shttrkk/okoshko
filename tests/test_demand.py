"""Cabinet demand comes from the local search log, never from a hash."""
from fastapi.testclient import TestClient

from backend.agents.demand import search_demand
from backend.db.repository import get_repo
from backend.main import app
from backend.tests.test_api_integration import ApiRepo, repo  # noqa: F401  (fixture)


def test_counts_only_searches_that_showed_the_hotel():
    store = ApiRepo()
    rows = [("a", ["h1", "h2"], ["sea_view", "beach_walk"]), ("b", ["h2"], ["sea_view"]), ("c", ["h1"], [])]
    for index, (text, results, wanted) in enumerate(rows):
        store.insert("search_queries", {"id": text, "text": text, "results": results, "created_at": f"2026-10-2{index}T10:00:00Z",
                                        "parsed": {"must": [{"characteristic_id": cid} for cid in wanted], "nice": []}})
    demand = search_demand(store, "h1")
    assert demand["total_searches"] == 3 and demand["synthetic"] is False
    assert demand["requested"] == {"sea_view": 2, "beach_walk": 1}
    assert demand["shown_with"] == {"sea_view": 1, "beach_walk": 1}
    assert [query["text"] for query in demand["queries"]] == ["c", "a"] and demand["queries"][1]["position"] == 1


def test_a_real_search_is_counted_for_the_hotels_it_showed(repo):  # noqa: F811
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        with TestClient(app) as client:
            shown = [hit["hotel"]["id"] for hit in client.post("/api/search", json={"text": "до пляжа 10 минут"}).json()["results"]]
    finally:
        app.dependency_overrides.clear()
    assert shown and search_demand(repo, shown[0])["shown_with"] == {"beach_walk": 1}
