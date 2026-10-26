from contextlib import contextmanager
from pathlib import Path

from backend.seed import seed_dialogues


class SeedConnection:
    def __init__(self):
        self.batches = []

    @contextmanager
    def cursor(self):
        yield self

    def executemany(self, query, rows):
        self.batches.append(rows)


def test_release_dialogues_load_through_typed_question_foreign_key():
    import json
    root = Path(__file__).resolve().parents[1]
    hotels = {hotel["id"] for hotel in json.loads((root/"data/seed/hotels.json").read_text())}
    conn = SeedConnection()
    seed_dialogues(conn, root/"data/seed/demo_hotelier_dialogues.json", hotels)
    assert [len(rows) for rows in conn.batches] == [9, 7, 7, 28, 7]
