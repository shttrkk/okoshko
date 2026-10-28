"""A Wi-Fi presence report keeps its actual source without inventing speed."""
from datetime import timedelta

import pytest

from backend.agents.answer_parser import parse_answer
from backend.agents.explainer import verdict
from backend.agents.interviewer import create_question
from backend.agents.query_match import assess_hotel
from backend.api.hotelier import confirm_answer
from backend.contracts import Criterion, Evidence, Fact, ParsedQuery, Question, now
from backend.db.repository import search_fact
from backend.security import DEMO_ADMIN
from backend.tests.test_hotelier import MemoryRepo

OPERATOR = 'Wi-Fi во всех номерах бесплатный. Скорость не измеряли.'
SPEED = Criterion(characteristic_id='connectivity', label='Связь для работы', field='download_mbps', op='gte', value=30)
QUERY = ParsedQuery(text='Нужен быстрый Wi-Fi', must=[SPEED])


def inspect(item, compact):
    if compact:
        item = Fact.model_validate(search_fact(item.model_dump(mode='json')))
    [match] = assess_hotel([item], QUERY).matches
    result = verdict(SPEED, match, True, QUERY)
    assert result.status == 'partial' and not match.condition_met and not match.supported
    assert not result.hotelier_confirmed
    assert 'скорость неизвестна' in result.text
    assert '30' not in result.text and 'Мбит' not in result.text
    return match, result


@pytest.mark.parametrize('compact', [False, True], ids=['full', 'compact-search'])
@pytest.mark.parametrize('kind,source,synthetic,quote,expected', [
    ('hotelier', 'hotelier', False, OPERATOR, 'Wi-Fi есть по ответу отеля · скорость неизвестна'),
    ('simulation', 'hotelier_simulator', True, OPERATOR, 'Wi-Fi есть по ответу отеля · демо · скорость неизвестна'),
    # Typed simulation provenance still prevents real-source wording even if
    # a malformed outer record omitted its synthetic flag.
    ('simulation', 'hotelier_simulator', False, OPERATOR, 'Wi-Fi есть по ответу отеля · демо · скорость неизвестна'),
    ('open_data', 'osm_geofabrik', False, None, 'Wi-Fi есть по карте · скорость неизвестна'),
    ('quote', 'hotel_website', False, 'На сайте указан Wi-Fi', 'Wi-Fi указан в источнике · скорость неизвестна'),
    ('open_data', 'unidentified_dataset', False, None, 'Wi-Fi указан в источнике · скорость неизвестна'),
    ('simulation', 'geo_engine', True, 'Example geometry', 'Wi-Fi указан в источнике · скорость неизвестна'),
])
def test_presence_text_uses_retained_typed_provenance(kind, source, synthetic, quote, expected, compact):
    item = Fact(id='wifi', hotel_id='hotel', characteristic_id='connectivity',
                value={'available': None, 'wifi_reported': True, 'download_mbps': None, 'upload_mbps': None, 'latency_ms': None},
                status='confirmed' if kind == 'hotelier' else 'computed', confidence=.85,
                synthetic=synthetic, supporting_claim_ids=['claim'], valid_until=now() + timedelta(days=1),
                evidence=[Evidence(id='wifi-proof', kind=kind, source_ids=[source], quote=quote,
                                   label='Known-answer source', artifact='/tests/known-answer.json',
                                   inputs={'raw_metadata': 'not retained in search'}, output={'measurement': None})])
    before = item.model_dump()
    match, result = inspect(item, compact)
    assert match.reason == result.text == expected
    if source == 'hotelier':
        assert result.basis == 'hotelier' and not result.synthetic
        assert 'Наличие Wi-Fi сообщил отель' in result.caveat and 'открытых данных' not in result.caveat
    elif source == 'hotelier_simulator':
        assert result.synthetic and 'демо-ответом отеля' in result.caveat
    if source != 'osm_geofabrik':
        assert 'по карте' not in result.text and 'указано в открытых данных' not in result.caveat
    assert item.model_dump() == before


@pytest.mark.parametrize('compact', [False, True], ids=['full', 'compact-search'])
@pytest.mark.parametrize('synthetic', [False, True], ids=['actual-operator', 'typed-demo'])
def test_published_operator_answer_keeps_quote_source_in_search_without_speed(compact, synthetic):
    repo = MemoryRepo()
    question = create_question('test-hotel', 'connectivity', repo)
    answer = parse_answer(Question.model_validate(question.model_dump()), OPERATOR, synthetic=synthetic)
    assert answer.disposition == 'parsed'
    repo.insert('answers', answer.model_dump(mode='json'))
    confirm_answer(answer.id, repo, principal=DEMO_ADMIN)
    facts = [Fact.model_validate(row) for row in repo.find('facts', hotel_id='test-hotel')
             if row['characteristic_id'] == 'connectivity' and isinstance(row.get('value'), dict)
             and row['value'].get('wifi_reported') is True]
    assert facts
    item = next(fact for fact in facts if any(e.quote and e.quote in OPERATOR for e in fact.evidence))
    _, result = inspect(item, compact)
    assert result.text == 'Wi-Fi есть по ответу отеля · ' + ('демо · ' if synthetic else '') + 'скорость неизвестна'
    assert result.basis == ('unknown' if synthetic else 'hotelier')
    assert result.synthetic is synthetic and not result.hotelier_confirmed
    assert item.value.get('download_mbps') is None and item.value.get('upload_mbps') is None
    assert item.value.get('latency_ms') is None
