import pytest

from snipetech.config import DEFAULT_OPERATOR_PRICES
from snipetech.operator.budget import Ledger, cost_usd

DAY = 1_793_923_200  # 2026-11-06 00:00:00 UTC


def test_cost_from_tokens():
    prices = {"in": 3.0, "out": 15.0, "cache_read": 0.3, "cache_write": 3.75}
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_read_input_tokens": 2_000_000}
    assert cost_usd(usage, prices, analyst=False) == pytest.approx(3.0 + 1.5 + 0.6)


def test_analyst_prices_are_used_for_analyst_turns():
    usage = {"input_tokens": 1_000_000}
    assert cost_usd(usage, DEFAULT_OPERATOR_PRICES, analyst=True) == pytest.approx(5.0)
    assert cost_usd(usage, DEFAULT_OPERATOR_PRICES, analyst=False) == pytest.approx(3.0)


def test_spent_today_respects_utc_day(tmp_path):
    led = Ledger(tmp_path / "spend.jsonl", DEFAULT_OPERATOR_PRICES)
    led.record("operator", {"input_tokens": 1_000_000}, now=DAY - 60)  # yesterday
    led.record("operator", {"input_tokens": 1_000_000}, now=DAY + 60)  # today
    assert led.spent_today(DAY + 3600) == pytest.approx(3.0)
    assert led.spent_since(DAY - 3600) == pytest.approx(6.0)


def test_record_returns_usd_and_writes_one_line(tmp_path):
    led = Ledger(tmp_path / "spend.jsonl", DEFAULT_OPERATOR_PRICES)
    usd = led.record("analyst", {"output_tokens": 1_000_000}, now=DAY)
    assert usd == pytest.approx(25.0)
    lines = (tmp_path / "spend.jsonl").read_text().splitlines()
    assert len(lines) == 1 and '"mode": "analyst"' in lines[0] and '"out": 1000000' in lines[0]


def test_missing_file_means_zero(tmp_path):
    assert Ledger(tmp_path / "none.jsonl", DEFAULT_OPERATOR_PRICES).spent_today(DAY) == 0.0


def test_none_token_counts_count_as_zero():
    usage = {"input_tokens": 1_000_000, "output_tokens": None, "cache_read_input_tokens": None, "cache_creation_input_tokens": None}
    assert cost_usd(usage, DEFAULT_OPERATOR_PRICES, analyst=False) == pytest.approx(3.0)
