"""The config file round-trips: None stays None (TOML has no null, so the key is left out), lists stay lists, and
setting a None-valued key from text gives a number. Found by the 6 Nov review: a saved file turned
oktech.compute_units into the string "None" and every sniper builder raised."""

import tomllib

from snipetech.config import Config, dump_toml


def test_none_and_lists_survive_save_and_load(tmp_path):
    p = tmp_path / "snipetech.toml"
    Config().save(p)
    text = p.read_text()
    assert "compute_units" not in text  # a None is left out, not written as a string
    again = Config.load(p)
    assert again.oktech.compute_units is None
    assert again.birdeye.gainers_periods == Config().birdeye.gainers_periods and isinstance(
        again.birdeye.gainers_periods, list
    )
    assert tomllib.loads(dump_toml({"a": None, "b": [1, 2], "c": {"d": None, "e": "x"}})) == {
        "b": [1, 2],
        "c": {"e": "x"},
    }


def test_setting_a_none_valued_key_from_text_gives_a_number():
    cfg = Config()
    assert cfg.set("oktech.compute_units", "90000") == 90000 and isinstance(cfg.oktech.compute_units, int)
    cfg2 = Config()
    assert cfg2.set("oktech.compute_units", "") is None


def test_every_builder_survives_a_saved_and_reloaded_config(tmp_path):
    from snipetech.oktech import list_copy_sniper

    p = tmp_path / "snipetech.toml"
    Config().save(p)
    cfg = Config.load(p)
    assert "computeUnits" not in list_copy_sniper("W" * 32, "lead", 0.5, 0.2, cfg=cfg).config["extraParams"]


def test_save_uses_a_private_temp_name(tmp_path, monkeypatch):
    import os

    p = tmp_path / "snipetech.toml"
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    Config().save(p)
    assert seen and str(os.getpid()) in seen[0]
