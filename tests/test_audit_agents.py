"""Review of the agents' audit fixes, 22 Nov: what an agent's job may write, beyond the fixes' own tests."""

import asyncio

from snipetech.store import Store

from .synth import addr

KOL, NEW, OLD, STUDIED = addr("st-kol"), addr("st-new"), addr("st-old"), addr("st-studied")


def test_a_screen_never_takes_a_wallets_name_of_another_kind(tmp_path):
    """A screen's own label (cand from fomo-wallets and early-leaders, smart from study) is written unless the wallet
    carries a label of another kind: a KOL, the owner's tracked list or one of ours keeps its name. The same kind is
    written again with the new name."""
    st = Store(tmp_path / "s.db")
    try:
        st.set_label(KOL, "Cupseyy", "kol", "file")
        assert st.set_label_unless_named(KOL, "fomo 12 buys", "cand", "fomo") is False
        assert st.set_label_unless_named(NEW, "fomo 10 buys", "cand", "fomo") is True
        assert st.set_label_unless_named(NEW, "early-leader n21", "cand", "early-leaders") is True
        assert st.label_kinds() == {KOL: ("kol", "Cupseyy"), NEW: ("cand", "early-leader n21")}
    finally:
        st.close()


def test_study_never_names_a_wallet_smart_over_the_name_it_has(tmp_path, monkeypatch):
    """Review of 22 Nov (agents#4, the same hole through another command): `study --auto-smart N` (the tester's, the
    analyst's and the operator's command) labelled the most consistently profitable wallets kind smart over whatever
    label they had, so a KOL or a tracked wallet among them left the groups kol, wallet and big. It keeps the name it
    has; a wallet with none, or one study named smart before, is named smart."""
    from snipetech import meta, service
    from snipetech.config import Config

    async def nothing(*_a, **_k):
        return 0

    st = Store(tmp_path / "s.db")
    try:
        st.set_label(KOL, "Cupseyy", "kol", "file")
        st.set_label(OLD, "smart", "smart", "auto")
        svc = service.Service(Config(), store=st)
        found = [{"wallet": w, "ev_lower": 0.1} for w in (KOL, NEW, OLD)]
        monkeypatch.setattr(svc, "discover_wallets", lambda days, n, limit: found)
        monkeypatch.setattr(service, "collect_study", lambda *_a: {"rows": {}})
        monkeypatch.setattr(service, "study_report", lambda *_a: {})
        monkeypatch.setattr(meta, "fetch_meta", nothing)
        monkeypatch.setattr(meta, "ensure_assets", nothing)
        asyncio.run(svc.study(STUDIED, days=1, refresh=False, auto_smart=3))
        assert st.label_kinds() == {KOL: ("kol", "Cupseyy"), NEW: ("smart", "smart"), OLD: ("smart", "smart")}
    finally:
        st.close()
