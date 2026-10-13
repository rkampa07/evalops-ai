import pytest

from evalops.engine.agreement import binary_agreement, wilson_interval


def test_confusion_matrix_and_rates():
    labels = ["u", "u", "u", "s", "s", "s", "s", "s"]
    preds = ["u", "u", "s", "u", "s", "s", "s", "s"]
    m = binary_agreement(labels, preds, positive="u")
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (2, 1, 1, 4)
    assert m["precision"] == pytest.approx(2 / 3)
    assert m["recall"] == pytest.approx(2 / 3)
    assert m["f1"] == pytest.approx(2 / 3)
    assert m["accuracy"] == pytest.approx(6 / 8)
    # kappa: po = .75, pe = (3/8*3/8) + (5/8*5/8) = .53125 -> (.75-.53125)/(1-.53125)
    assert m["cohen_kappa"] == pytest.approx((0.75 - 0.53125) / (1 - 0.53125))
    assert m["n"] == 8


def test_undefined_rates_are_none():
    m = binary_agreement(["s", "s"], ["s", "s"], positive="u")
    assert m["precision"] is None and m["recall"] is None and m["f1"] is None
    assert m["accuracy"] == 1.0 and m["cohen_kappa"] is None  # pe = 1


def test_wilson_interval():
    lo, hi = wilson_interval(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)
    assert wilson_interval(0, 0) == (None, None)


def test_length_mismatch():
    with pytest.raises(ValueError):
        binary_agreement(["u"], [], positive="u")
