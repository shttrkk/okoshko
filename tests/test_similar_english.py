"""Bounded English requests resolve exact catalogue names and retain wishes."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.agents.query_parser import parse_query
from backend.agents.similarity import only_reference_phrase, reference_hotel
from backend.contracts import Hotel, SearchRequest
from backend.db.repository import get_repo
from backend.main import app
from backend.tests.test_api_integration import repo  # noqa: F401
from backend.tests.test_search import fact
from scripts.parser_eval import catalogue


def hotels(*names):
    return [Hotel(id=f'english-{number}', name=name, lat=43.58, lon=39.72) for number,name in enumerate(names)]


@pytest.mark.parametrize('header', ['Something like', 'similar to', 'Find hotels like',
                                    'Show a hotel like', 'Please show us some hotels similar to',
                                    'I need something like'])
def test_english_similarity_headers_use_exact_verified_names(header):
    [expected] = hotels('Cove Garden')
    reference, ambiguous = reference_hotel(header+' Cove Garden', [expected])
    assert reference == expected and not ambiguous
    assert only_reference_phrase(header+' Cove Garden', expected.name)


def test_longest_complete_name_wins_before_a_real_condition():
    catalog = hotels('Cove', 'Cove and Garden')
    reference, ambiguous = reference_hotel('Something like "Cove and Garden", but quiet at night', catalog)
    assert reference == catalog[1] and not ambiguous
    assert not only_reference_phrase('Something like Cove and Garden but quiet at night', reference.name)


@pytest.mark.parametrize('text', ['Unlike Cove Garden', 'not like Cove Garden',
                                 "I don't like Cove Garden", 'I do not like Cove Garden',
                                 'I like Cove Garden', 'Do not find hotels like Cove Garden',
                                 'Find hotels not like Cove Garden', 'Find hotels unlike Cove Garden',
                                 'We discussed something like Cove Garden',
                                 'not similar to Cove Garden', 'similar to Cove Gardn',
                                 'something like Cove', 'something like Cove Garden Annex'])
def test_negation_affection_mentions_and_incomplete_names_do_not_guess_reference(text):
    assert reference_hotel(text, hotels('Cove Garden')) == (None, [])


def test_duplicate_exact_names_are_ambiguous_instead_of_guessing_an_id():
    catalog = hotels('Cove Garden', 'Cove Garden')
    reference, ambiguous = reference_hotel('similar to Cove Garden', catalog)
    assert reference is None and ambiguous == ['Cove Garden']


@pytest.mark.parametrize('text', ['Something like Cove Garden but a pool is essential',
                                 'Show hotels like Cove Garden with a sauna',
                                 'similar to Cove Garden and price below 9000',
                                 'I like Cove Garden', 'Cove Garden but a balcony'])
def test_reference_cleanup_keeps_unrecognized_desires(text):
    assert not only_reference_phrase(text, 'Cove Garden')


@pytest.mark.parametrize('fragment', ['show like cove garden', 'something like cove garden but',
                                     'find hotels like synthetic'])
def test_cleanup_recognizes_only_header_and_verified_name_tokens_after_parser_filtering(fragment):
    name = 'Synthetic 2' if 'synthetic' in fragment else 'Cove Garden'
    assert only_reference_phrase(fragment, name)


def prepare_reference(store):
    reference = Hotel(id='english-reference', name='Brevis apartments', lat=43.58, lon=39.72)
    store.upsert('hotels', reference.model_dump(mode='json'))
    for row in store.all('hotels'):
        observed = fact(row['id'], 'green_nearby', {'available': True, 'distance_m': 200})
        store.upsert('facts', observed.model_dump(mode='json'))
    return reference


def test_english_api_keeps_threshold_trip_dates_and_verified_name_after_edit(repo):  # noqa: F811
    reference = prepare_reference(repo)
    text = 'Something like Brevis apartments, but a park within 250 m is required, 17–22 February 2028.'
    baseline = parse_query(SearchRequest(text=text), catalogue())
    assert baseline.check_in == date(2028, 2, 17) and baseline.check_out == date(2028, 2, 22)
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        with TestClient(app) as client:
            response = client.post('/api/search', json={'text': text})
            assert response.status_code == 200, response.text
            body = response.json()
            parsed = body['parsed']
            assert parsed['similar_to'] == reference.id and parsed['similar_to_name'] == reference.name
            assert parsed['check_in'] == '2028-02-17' and parsed['check_out'] == '2028-02-22' and parsed['month'] == 2
            assert [(c['characteristic_id'],c['field'],c['op'],c['value']) for c in parsed['must']] == [
                ('green_nearby','distance_m','lte',250)]
            assert body['results'] and all(hit['summary'].startswith('Сходство профиля') for hit in body['results'])
            assert reference.id not in {hit['hotel']['id'] for hit in body['results']}
            parsed['similar_to_name'] = 'forged client label'
            edited = client.post('/api/search', json={'text': text, 'parsed': parsed})
            assert edited.status_code == 200, edited.text
            assert edited.json()['parsed']['similar_to_name'] == reference.name
            assert edited.json()['parsed']['must'] == body['parsed']['must']
    finally:
        app.dependency_overrides.clear()


def test_english_api_retains_unsupported_wish_and_clarification(repo):  # noqa: F811
    reference = prepare_reference(repo)
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        with TestClient(app) as client:
            response = client.post('/api/search', json={'text': 'Find hotels like Brevis apartments, but a unicorn-shaped bathtub is required'})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    parsed = response.json()['parsed']
    assert parsed['similar_to'] == reference.id
    assert any('bathtub' in fragment for fragment in parsed['unmapped']) and parsed['clarifications']


def test_english_api_asks_to_disambiguate_catalogue_names(repo):  # noqa: F811
    for hotel in hotels('Cove Garden', 'COVE GARDEN'):
        repo.upsert('hotels', hotel.model_dump(mode='json'))
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        with TestClient(app) as client:
            response = client.post('/api/search', json={'text': 'similar to Cove Garden'})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    parsed = response.json()['parsed']
    assert parsed['similar_to'] is None
    assert any('Cove Garden' in message and 'COVE GARDEN' in message for message in parsed['clarifications'])
