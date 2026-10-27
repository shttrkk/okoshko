"""Reviewed points cannot re-enter search, comparison or booking through direct IDs."""
from backend.tests.test_api_integration import client, repo  # noqa: F401

HELD = 'overture-129b6d6a-76ce-4140-8495-3eca01fa691d'


def add_held(repository):
    row = {**repository.get('hotels', 'test-hotel'), 'id': HELD, 'name': 'Сочи. Пляж Отеля Звездный'}
    repository.insert('hotels', row)


def test_public_catalog_and_coverage_use_only_eligible_objects(client, repo):  # noqa: F811
    add_held(repo)
    assert HELD not in {hotel['id'] for hotel in client.get('/api/hotels').json()}
    status = client.get('/api/status').json()
    assert status['hotel_count'] == 3 and status['source_object_count'] == 4 and status['held_object_count'] == 1
    result = client.post('/api/search', json={'text': '', 'limit': 40}).json()
    assert HELD not in {hit['hotel']['id'] for hit in result['results']}


def test_direct_profile_explains_hold_without_a_room_promise(client, repo):  # noqa: F811
    add_held(repo)
    response = client.get('/api/hotels/'+HELD+'?compact=true')
    assert response.status_code == 200
    profile = response.json()
    assert profile['hotel']['tags']['catalogue_review']['disposition'] == 'hold_for_identity_review'
    assert profile['verdicts'] == []
    evaluated = client.post('/api/hotels/'+HELD+'/evaluate?compact=true', json={'text': '', 'must': []})
    assert evaluated.status_code == 200 and evaluated.json()['verdicts'] == []
    geometry = client.get('/api/hotels/'+HELD+'/geo').json()
    assert not geometry['facades'] and geometry['limitations']


def test_direct_reference_compare_and_passport_are_rejected(client, repo):  # noqa: F811
    add_held(repo)
    assert client.post('/api/search', json={'similar_to': HELD}).status_code == 422
    assert client.post('/api/search', json={'text': 'Похожий на Сочи. Пляж Отеля Звездный'}).status_code == 422
    assert client.get('/api/compare?ids='+HELD+',test-hotel').status_code == 422
    assert client.post('/api/compare', json={'ids': [HELD, 'test-hotel']}).status_code == 422
    assert client.post('/api/passports', json={'hotel_id': HELD, 'guest_name': 'Тест', 'items': []}).status_code == 422
    assert not repo.all('expectation_passports')


def test_sources_publish_review_evidence_and_qualified_confidence(client):  # noqa: F811
    reviews = [row for row in client.get('/api/sources').json() if row['id'] == 'catalogue_identity_review']
    assert len(reviews) == 1 and reviews[0]['url'] == '/data/seed/catalogue_reviews.json'
    assert 'некалиброванные' in reviews[0]['notes']
