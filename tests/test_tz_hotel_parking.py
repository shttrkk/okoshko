import pytest

from backend.agents.answer_parser import parse_answer
from backend.agents.interviewer import _build_question
from backend.contracts import Question


def question():
    return Question(id='q', hotel_id='h', characteristic_ids=['hotel_parking'], text='Есть ли своя парковка?')


@pytest.mark.parametrize('text,expected', [
    ('Своя парковка есть. Стоимость 500 рублей за сутки. Бронировать нужно. Зарядка есть.',
     {'private_parking': True, 'fee_rub_per_day': 500, 'reservation_required': True, 'ev_charging': True}),
    ('Своей парковки нет', {'private_parking': False}),
    ('Нет', {'private_parking': False}),
    ('Парковка бесплатная, бронировать не нужно, зарядки нет',
     {'private_parking': True, 'fee_rub_per_day': 0, 'reservation_required': False, 'ev_charging': False}),
])
def test_free_hotel_answers_extract_independent_parking_fields_and_verbatim_quotes(text, expected):
    answer = parse_answer(question(), text)
    [update] = [u for u in answer.updates if u.characteristic_id == 'hotel_parking']
    assert update.value == expected
    assert update.quote in text
    assert update.scope.facade is None and update.scope.floor_min is None


@pytest.mark.parametrize('text', ['Не знаю', 'Рядом общественная парковка в 100 м', 'Возможно, своя парковка есть',
    'Рядом общественная парковка за 500 рублей в сутки, нужно бронировать',
    'Номер стоит 5000 рублей за сутки'])
def test_public_or_uncertain_parking_does_not_become_private_service(text):
    answer = parse_answer(question(), text)
    assert not answer.updates
    assert answer.unknown_fields


def test_one_time_price_is_not_rewritten_as_a_daily_fee():
    answer = parse_answer(question(), 'Своя парковка есть, 1000 рублей разово')
    assert answer.updates[0].value == {'private_parking': True}
    assert 'hotel_parking.fee_rub_per_day' in answer.unknown_fields


def test_parking_prompt_is_human_and_preserves_demand_priority():
    q = _build_question('h', 'hotel_parking', [], spec={'name_ru': 'Своя парковка'}, demand=4)
    assert 'своя парковка' in q.text and 'за сутки' in q.text and 'бронировать' in q.text
    assert q.demand_estimate == 4 and not q.demand_synthetic


def test_partial_blackout_answer_does_not_claim_insulation():
    q = Question(id='q', hotel_id='h', characteristic_ids=['soundproofing'], text='Есть ли плотные шторы?')
    answer = parse_answer(q, 'Светонепроницаемые шторы есть, шумоизоляцию не проверяли')
    assert answer.updates[0].value['blackout'] is True
    assert answer.updates[0].value['available'] is None
