"""#231: the fulltime-faculty OR rule is OFF by default. Lock the publish-safe
default against (a) a config flip and (b) a threshold-load failure.

Kept in a dedicated module with NO autouse flag fixture so these assertions
exercise the REAL _faculty_or_enabled() (and the shipped config/thresholds.json),
not a mocked flag. Flipping the flag on would emit empty-pid AuthorPairs and fail
publish.py's Author.personIdentifier minLength:1 schema gate.
"""
import spotlight.author_resolver as ar
import utils.env_check as env_check


def test_live_config_default_is_strict_and():
    """The shipped config/thresholds.json must keep the OR rule OFF."""
    assert ar._faculty_or_enabled() is False


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
