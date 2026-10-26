import pytest

from backend.quality import classification, evaluate, ndcg


def test_metrics_do_not_present_missing_labels_as_zero_accuracy():
    report = evaluate([], [], [], [])
    assert report["status"] == "awaiting_independent_labels"
    assert report["ndcg_at_5"] is None
    assert report["label_groups"] == {}


def test_classification_and_ndcg_have_known_answers():
    metrics = classification([(True, True, .8), (False, False, .2), (True, False, .7)], 1)
    assert metrics["accuracy"] == pytest.approx(2/3)
    assert metrics["precision"] == .5
    assert metrics["recall"] == 1
    assert metrics["brier"] == pytest.approx((.04+.04+.49)/3)
    assert ndcg(["a", "b"], {"a": 2, "b": 1}) == 1
    assert ndcg(["b", "a"], {"a": 2, "b": 1}) < 1


def test_calibration_bins_count_every_example_once():
    metrics = classification([(True, True, .6),(False,False,.2),(True,True,1.0)])
    assert sum(row["n"] for row in metrics["calibration"]) == 3


def test_website_cannot_evaluate_its_own_quote():
    hotel={"id":"h","floors":5}
    fact={"id":"f","hotel_id":"h","characteristic_id":"sea_view","value":{"available":True},"status":"computed","confidence":.9,"evidence":[{"kind":"quote"}]}
    label={"hotel_id":"h","characteristic_id":"sea_view","value":{"available":True},"label_type":"weak_website"}
    report=evaluate([hotel],[fact],[{"id":"sea_view"}],[label])
    assert report["label_groups"]["weak_website"]["evaluated"]==0
    assert report["label_groups"]["weak_website"]["accuracy"] is None

