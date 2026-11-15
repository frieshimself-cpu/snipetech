import json

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
    assert cost_usd(usage, DEFAULT_OPERATOR_PRICES, analyst=True) == pytest.approx(8.0)
    assert cost_usd(usage, DEFAULT_OPERATOR_PRICES, analyst=False) == pytest.approx(2.0)


def test_each_agent_is_priced_at_its_own_models_rates(tmp_path):
    """10 Nov: the analyst moved to a model at twice the strategist's price; the strategist's line no longer borrows
    the analyst's rates."""
    led = Ledger(tmp_path / "spend.jsonl", DEFAULT_OPERATOR_PRICES)
    out = {"output_tokens": 1_000_000}
    assert led.record("operator", out, now=DAY) == pytest.approx(10.0)
    assert led.record("analyst", out, now=DAY) == pytest.approx(40.0)
    assert led.record("postmortem", out, now=DAY) == pytest.approx(40.0)  # an analyst turn by another name
    assert led.record("strategist", out, now=DAY) == pytest.approx(20.0)
    assert cost_usd(
        {"cache_read_input_tokens": 1_000_000}, DEFAULT_OPERATOR_PRICES, tier="strategist"
    ) == pytest.approx(0.2)


def test_spent_today_respects_utc_day(tmp_path):
    led = Ledger(tmp_path / "spend.jsonl", DEFAULT_OPERATOR_PRICES)
    led.record("operator", {"input_tokens": 1_000_000}, now=DAY - 60)  # yesterday
    led.record("operator", {"input_tokens": 1_000_000}, now=DAY + 60)  # today
    assert led.spent_today(DAY + 3600) == pytest.approx(2.0)
    assert led.spent_since(DAY - 3600) == pytest.approx(4.0)


def test_record_returns_usd_and_writes_one_line(tmp_path):
    led = Ledger(tmp_path / "spend.jsonl", DEFAULT_OPERATOR_PRICES)
    usd = led.record("analyst", {"output_tokens": 1_000_000}, now=DAY)
    assert usd == pytest.approx(40.0)
    lines = (tmp_path / "spend.jsonl").read_text().splitlines()
    assert len(lines) == 1 and '"mode": "analyst"' in lines[0] and '"out": 1000000' in lines[0]


def test_missing_file_means_zero(tmp_path):
    assert Ledger(tmp_path / "none.jsonl", DEFAULT_OPERATOR_PRICES).spent_today(DAY) == 0.0


def test_none_token_counts_count_as_zero():
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": None,
        "cache_read_input_tokens": None,
        "cache_creation_input_tokens": None,
    }
    assert cost_usd(usage, DEFAULT_OPERATOR_PRICES, analyst=False) == pytest.approx(2.0)


def test_an_hours_cache_write_is_priced_at_its_own_rate(tmp_path):
    """11 Nov: the tools and the instructions are written to the hour's cache, at twice the input price where the
    five-minute write is 1.25 times it (Claude Fable 5.2: $16 and $10 a million, the pricing page that day). The usage
    line splits the written tokens by duration; the ledger prices each part and keeps the hour's count."""
    import json

    usage = {"cache_creation_input_tokens": 1_500_000,
             "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000, "ephemeral_5m_input_tokens": 500_000}}  # fmt: skip
    assert cost_usd(usage, DEFAULT_OPERATOR_PRICES, tier="analyst") == pytest.approx(16.0 + 5.0)
    assert cost_usd(usage, DEFAULT_OPERATOR_PRICES) == pytest.approx(4.0 + 1.25)
    assert cost_usd(usage, DEFAULT_OPERATOR_PRICES, tier="strategist") == pytest.approx(8.0 + 2.5)
    old = {"in": 3.0, "out": 15.0, "cache_read": 0.3, "cache_write": 3.75}  # a price table without the hour's row
    assert cost_usd(usage, old) == pytest.approx(3.75 * 1.6 + 3.75 * 0.5)  # 2 x input over 1.25 x input
    led = Ledger(tmp_path / "spend.jsonl", DEFAULT_OPERATOR_PRICES)
    led.record("analyst", usage, now=DAY)
    line = json.loads((tmp_path / "spend.jsonl").read_text())
    assert line["cache_write"] == 1_500_000 and line["cache_write_1h"] == 1_000_000 and line["usd"] == 21.0


def test_role_turn_is_priced_like_the_analyst(tmp_path):
    """A research agent's turn runs on the analyst's model: its spend is priced at the analyst's rows, under its own
    name in the ledger."""
    from snipetech.operator.budget import TIERS
    from snipetech.operator.roles import ROLES

    led = Ledger(tmp_path / "spend.jsonl", DEFAULT_OPERATOR_PRICES)
    for role in ROLES:
        assert TIERS[role] == "analyst"
        assert led.record(role, {"output_tokens": 1_000_000}, now=DAY) == pytest.approx(40.0)
    assert '"mode": "scout"' in (tmp_path / "spend.jsonl").read_text().splitlines()[0]


def test_spent_today_by_role(tmp_path):
    led = Ledger(tmp_path / "spend.jsonl", DEFAULT_OPERATOR_PRICES)
    led.record("scout", {"output_tokens": 100_000}, now=DAY - 60)  # yesterday
    led.record("scout", {"output_tokens": 100_000}, now=DAY + 60)
    led.record("scout", {"output_tokens": 50_000}, now=DAY + 120)
    led.record("tester", {"output_tokens": 200_000}, now=DAY + 60)
    led.record("operator", {"input_tokens": 1_000_000}, now=DAY + 60)
    by = led.spent_today_by(DAY + 3600)
    assert by == pytest.approx({"scout": 6.0, "tester": 8.0, "operator": 2.0})
    assert sum(by.values()) == pytest.approx(led.spent_today(DAY + 3600))
    assert Ledger(tmp_path / "none.jsonl", DEFAULT_OPERATOR_PRICES).spent_today_by(DAY) == {}


# -- 14 Nov review: a response is priced by the model that answered, and every attempt of it is counted ----------------
# `fallbacks="default"` lets the API serve a request the asked model declined on another model inside the same call.
# The API's reference (the claude-api skill's shared/model-migration.md, "Billing", line 1443 of the copy read on
# 14 Nov): "`usage.iterations` is the per-attempt source of truth; top-level `usage` covers only the attempt that
# produced the returned message ... fallback attempts bill at the fallback model's rates." The ledger priced every
# response of a mode at the mode's rows and read the top-level usage only.


def test_a_response_another_model_served_is_priced_per_attempt_at_each_models_rows(tmp_path):
    from snipetech.operator.budget import MODEL_PRICES

    led = Ledger(tmp_path / "spend.jsonl", DEFAULT_OPERATOR_PRICES)
    declined = {"type": "message", "model": "claude-fable-5-2", "input_tokens": 100_000, "output_tokens": 0,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}  # fmt: skip
    served = {"type": "fallback_message", "model": "claude-opus-5-5", "input_tokens": 100_000, "output_tokens": 10_000,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}  # fmt: skip
    usage = {"input_tokens": 100_000, "output_tokens": 10_000, "iterations": [declined, served]}
    # the asked model's attempt at the analyst's rows (8 $ in), the serving one at the strategist's (4 $ in, 20 $ out)
    want = 100_000 * 8 / 1e6 + (100_000 * 4 + 10_000 * 20) / 1e6
    assert led.record("scout", usage, 1_000.0, model="claude-opus-5-5") == pytest.approx(want) == pytest.approx(1.4)
    line = json.loads((tmp_path / "spend.jsonl").read_text())
    assert line["usd"] == pytest.approx(want) and line["mode"] == "scout" and line["in"] == 100_000
    assert line["model"] == "claude-opus-5-5" and line["attempts"] == 2  # said where it was not the mode's own model
    # a model the price table has no rows for, but that the API may fall back to: its own listed prices
    served_48 = served | {"model": "claude-opus-4-8"}
    p = MODEL_PRICES["claude-opus-4-8"]
    assert p["in"] == 5.0 and p["out"] == 25.0 and MODEL_PRICES["claude-opus-5"] == p
    assert cost_usd({"iterations": [declined, served_48]}, DEFAULT_OPERATOR_PRICES, tier="analyst") == pytest.approx(
        0.8 + (100_000 * 5 + 10_000 * 25) / 1e6)  # fmt: skip
    # a dated id of a known model is that model; a longer name is not taken for a shorter one
    dated = served | {"model": "claude-opus-5-5-20261101"}
    assert cost_usd({"iterations": [dated]}, DEFAULT_OPERATOR_PRICES, tier="analyst") == pytest.approx(0.6)
    # an attempt's writes to the hour's cache keep their own price
    wrote = declined | {"cache_creation_input_tokens": 1000, "cache_creation": {"ephemeral_1h_input_tokens": 1000}}
    assert cost_usd({"iterations": [wrote]}, DEFAULT_OPERATOR_PRICES, tier="analyst") == pytest.approx(0.8 + 0.016)


def test_an_unknown_model_is_priced_at_the_modes_rows(tmp_path):
    led = Ledger(tmp_path / "spend.jsonl", DEFAULT_OPERATOR_PRICES)
    usage = {"input_tokens": 1_000_000, "output_tokens": 0}
    assert led.record("scout", usage, 1_000.0, model="claude-some-new-model") == pytest.approx(8.0)
    attempt = {"type": "message", "model": "claude-some-new-model", "input_tokens": 1_000_000, "output_tokens": 0}
    twice = usage | {"iterations": [attempt, dict(attempt, model=None)]}
    assert led.record("scout", twice, 1_000.0) == pytest.approx(16.0)
    assert led.record("operator", usage, 1_000.0, model=None) == pytest.approx(2.0)
    lines = [json.loads(line) for line in (tmp_path / "spend.jsonl").read_text().splitlines()]
    assert all("model" not in line for line in lines)  # nothing to say: the mode's own rows
    # the top-level usage of a response without attempts is the mode's, whatever model the response names: only the
    # list of attempts says that another model was paid for
    assert led.record("scout", usage, 1_000.0, model="claude-sonnet-5-5") == pytest.approx(8.0)
    # a list of attempts that is empty or holds no counts changes nothing
    for empty in ([], None, [{}], "x"):
        assert cost_usd(usage | {"iterations": empty}, DEFAULT_OPERATOR_PRICES, tier="analyst") == pytest.approx(8.0)
