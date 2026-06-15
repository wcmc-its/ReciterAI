"""#231/#233: the fulltime-faculty OR rule. As of #233 it is ENABLED in the
shipped config (the schema's Author.personIdentifier minLength was relaxed in the
same change). These tests lock the live config state and the fail-closed
behavior, exercising the REAL _faculty_or_enabled() against the shipped
config/thresholds.json (no mocked flag) so an accidental revert is caught.
"""
import spotlight.author_resolver as ar
import utils.env_check as env_check


def test_live_config_enables_or():
    """The shipped config/thresholds.json enables the OR rule (#233 — the schema
    was relaxed in lockstep; see test_spotlight_or_preflight for coherence)."""
    assert ar._faculty_or_enabled() is True


def test_faculty_or_fails_closed_on_threshold_error(monkeypatch):
    """Any thresholds-load failure resolves to the publish-safe strict-AND rule."""
    def _boom(*a, **k):
        raise RuntimeError("thresholds unreadable")

    monkeypatch.setattr(env_check, "load_thresholds", _boom)
    assert ar._faculty_or_enabled() is False


def test_flag_true_in_config_enables_or(monkeypatch):
    """Sanity: the dispatch reads the flag — a true value enables the OR rule."""
    monkeypatch.setattr(env_check, "load_thresholds",
                        lambda *a, **k: {"spotlight_b1_faculty_or_enabled": True})
    assert ar._faculty_or_enabled() is True
