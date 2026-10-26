"""Public view signals must be stable on reads and never leak stored objects."""
from backend.tests.test_api_integration import client, repo  # noqa: F401


def test_revision_is_stable_and_not_cached(client, repo):  # noqa: F811
    generation = [7]
    repo.search_generation = lambda: generation[0]
    first = client.get('/api/revision')
    assert first.status_code == 200
    assert first.headers['Cache-Control'] == 'no-store'
    assert set(first.json()) == {'instance', 'revision'}
    assert first.json()['revision'] == 7
    assert isinstance(first.json()['instance'], str) and first.json()['instance']
    assert client.get('/api/revision').json() == first.json()
    generation[0] += 1
    assert client.get('/api/revision').json() == {**first.json(), 'revision': 8}


def test_revision_keeps_lightweight_repositories_compatible(client):  # noqa: F811
    assert client.get('/api/revision').json()['revision'] == 0
