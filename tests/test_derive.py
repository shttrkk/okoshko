import pytest

from backend.tools.derive import MissingInput, evaluate_expression, validate_expression


def test_composite_known_answer_and_unknown():
    values = {"beach_walk": {"minutes": 8}, "climate": {"water_c": 22}}
    assert evaluate_expression("beach_walk.minutes <= 10 and climate.water_c >= 20", values)
    with pytest.raises(MissingInput):
        evaluate_expression("beach_walk.minutes <= 10 and climate.water_c >= 20", {"beach_walk": {"minutes": 8}})


@pytest.mark.parametrize("expression", ["__import__('os').system('id')", "x.__class__", "[x for x in y]", "x[0]", "open('file')"])
def test_compiler_cannot_turn_text_into_code(expression):
    with pytest.raises(ValueError):
        validate_expression(expression)

