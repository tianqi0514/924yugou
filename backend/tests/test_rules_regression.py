"""Numerical regressions found during complete workflow QA; no database writes."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, getcontext

import pytest

from app.rules import EvalValue, RuleError, evaluate, unit_dimension


@pytest.mark.parametrize("expression", ["min(a,b)", "max(a,b)", "min(0,a,b)", "max(0,a,b)",
                                        "a*1+b", "a/1+b", "min(a*1,b)", "a/b>1"])
def test_min_max_reject_mixed_currency_scales(expression):
    values = {"a": EvalValue(Decimal("10"), unit_dimension("元"), "元"),
              "b": EvalValue(Decimal("2"), unit_dimension("万元"), "万元")}
    with pytest.raises(RuleError, match="单位不一致"):
        evaluate(expression, values)


def test_unit_guards_keep_zero_and_single_argument_valid():
    values = {"a": EvalValue(Decimal("0"), unit_dimension("套"), "套")}
    for expression in ("min(a)", "max(0,a)", "min(a,0)"):
        result = evaluate(expression, values)
        assert result.value == 0 and result.unit_tag == "套"
        assert result.dimension == unit_dimension("套")


def test_worker_thread_precision_is_stable_and_does_not_change_caller_context():
    def calculate():
        getcontext().prec = 9
        result = evaluate("a / 7", {"a": EvalValue(Decimal(1))})
        assert getcontext().prec == 9
        return result.value
    with ThreadPoolExecutor(max_workers=1) as pool:
        value = pool.submit(calculate).result()
    assert str(value) == "0." + "142857" * 8 + "14"


def test_arithmetic_overflow_is_an_explainable_rule_error():
    with pytest.raises(RuleError, match="超出可计算范围"):
        evaluate("a * 1e1000", {"a": EvalValue(Decimal("1e1000"))})


@pytest.mark.parametrize("expression", ["max(a,1e100000000)", "a * 1e-1000 * 1e-1000"])
def test_numeric_limits_cannot_be_bypassed_or_silently_become_zero(expression):
    with pytest.raises(RuleError, match="超出可计算范围"):
        evaluate(expression, {"a": EvalValue(Decimal(1))})


def test_number_display_is_independent_of_worker_precision():
    from app.writing import fits_decimal_places, format_number

    def render():
        getcontext().prec = 9
        result = format_number(Decimal("123456789012345678901234567890.125"), {"decimals": 2})
        assert fits_decimal_places(Decimal("123456789012345678901234567890.1200"), 2)
        assert not fits_decimal_places(Decimal("123456789012345678901234567890.125"), 2)
        assert getcontext().prec == 9
        return result
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(render).result() == "123456789012345678901234567890.13"


def test_composed_units_retain_scale_and_cancel_matching_symbols():
    values = {
        "price": EvalValue(Decimal("2"), unit_dimension("万元/条"), "万元/条"),
        "lines": EvalValue(Decimal("3"), unit_dimension("条"), "条"),
        "yuan": EvalValue(Decimal("1"), unit_dimension("元"), "元"),
    }
    total = evaluate("price * lines", values)
    assert total.value == 6 and total.unit_tag == "万元" and total.dimension == unit_dimension("万元")
    with pytest.raises(RuleError, match="单位不一致"):
        evaluate("price * lines + yuan", values)
    ratio = evaluate("lines / lines", values)
    assert ratio.value == 1 and ratio.unit_tag == "" and ratio.dimension == ()


def test_large_exact_operations_fail_explicitly_instead_of_silently_rounding():
    with pytest.raises(RuleError, match="不能无损计算"):
        evaluate("a+1", {"a": EvalValue(Decimal("1e1000"))})
    precise = Decimal("1" + "0" * 60 + "1")
    assert evaluate("-a", {"a": EvalValue(precise)}).value == precise.copy_negate()
