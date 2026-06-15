"""#231: B1 resolver uses the OR rule on fullTimeFaculty, not strict-AND presence.

The spotlight pool's B1 gate was "BOTH first AND last have a non-empty
personIdentifier" (presence-as-faculty proxy). The operator's actual rule is
"first OR last author is identity.fullTimeFaculty='yes'". These tests pin:

- the SQL joins ``identity`` on ``cwid = personIdentifier`` and reads
  ``fullTimeFaculty`` (no longer presence-as-proxy);
- a PMID qualifies when the first OR the last author is fulltime faculty;
- a PMID with NO fulltime-faculty lead is skipped AND logged
  (``no_fulltime_faculty_lead``) — flag-and-log, substrate untouched;
- "show both" — the AuthorPair still carries both byline leads; the
  non-faculty co-lead is emitted as-is (its pid may be empty);
- the displayed pid prefers the fulltime-faculty author at a position so the
  SPS headshot join lands on the faculty member.
"""
import logging

import pytest

import spotlight.author_resolver as ar

BYLINE = "Doe J, Roe K, ..., Smith A"


@pytest.fixture(autouse=True)
def _enable_or(monkeypatch):
    """These tests exercise the #231 OR rule, which is OFF by default. Enable it
    for the module; the flag-off default is locked separately below."""
    monkeypatch.setattr(ar, "_faculty_or_enabled", lambda: True)


def _engine_returning(rows, capture=None):
    """Fake SQLAlchemy engine whose connection yields ``rows`` from fetchall().

    ``capture`` (optional dict) records the executed query under key 'sql'.
    Row tuples are (pmid, personIdentifier, authorPosition, authors,
    fullTimeFaculty).
    """

    class _R:
        def fetchall(self):
            return rows

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, query, *a, **k):
            if capture is not None:
                capture["sql"] = str(query)
            return _R()

    class _Engine:
        def connect(self):
            return _Conn()

    return _Engine()


def test_sql_joins_identity_on_fulltime_faculty():
    """The query must LEFT JOIN identity on cwid and read fullTimeFaculty."""
    cap = {}
    ar.resolve_authors(["1"], engine=_engine_returning([], capture=cap))
    sql = cap["sql"].lower()
    assert "identity" in sql
    assert "fulltimefaculty" in sql.replace(" ", "")
    assert "cwid" in sql
    # LEFT join so the non-faculty co-lead's row survives for "show both".
    assert "left join" in sql


def test_first_only_faculty_qualifies_and_shows_both():
    rows = [
        ("100", "fac_first", "first", BYLINE, "yes"),
        ("100", "ext_last", "last", BYLINE, None),  # non-faculty co-lead
    ]
    out = ar.resolve_authors(["100"], engine=_engine_returning(rows))
    assert "100" in out
    pair = out["100"]
    assert pair.first_person_identifier == "fac_first"
    # "show both as-is": the non-faculty last author is still emitted.
    assert pair.last_person_identifier == "ext_last"
    # display names come from the byline parse (first / last positions).
    assert pair.first_display_name == "Doe J"
    assert pair.last_display_name == "Smith A"


def test_last_only_faculty_qualifies_and_shows_both():
    rows = [
        ("101", "ext_first", "first", BYLINE, "no"),
        ("101", "fac_last", "last", BYLINE, "yes"),
    ]
    out = ar.resolve_authors(["101"], engine=_engine_returning(rows))
    assert "101" in out
    assert out["101"].first_person_identifier == "ext_first"
    assert out["101"].last_person_identifier == "fac_last"


def test_both_faculty_qualifies():
    rows = [
        ("102", "fac_first", "first", BYLINE, "yes"),
        ("102", "fac_last", "last", BYLINE, "yes"),
    ]
    out = ar.resolve_authors(["102"], engine=_engine_returning(rows))
    assert "102" in out


def test_neither_faculty_is_skipped():
    rows = [
        ("103", "ext_first", "first", BYLINE, None),
        ("103", "ext_last", "last", BYLINE, "no"),
    ]
    out = ar.resolve_authors(["103"], engine=_engine_returning(rows))
    assert "103" not in out


def test_skip_is_logged_with_reason(caplog):
    rows = [
        ("104", "ext_first", "first", BYLINE, None),
        ("104", "ext_last", "last", BYLINE, None),
    ]
    with caplog.at_level(logging.INFO):
        ar.resolve_authors(["104"], engine=_engine_returning(rows))
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "no_fulltime_faculty_lead" in msgs
    assert "104" in msgs  # the skipped pmid appears in the audit sample line


def test_external_first_with_empty_pid_shows_both_when_last_is_faculty():
    """OR-unlock: external first author (empty pid) + faculty last author."""
    rows = [
        ("105", "", "first", BYLINE, None),  # external, no WCM identity
        ("105", "fac_last", "last", BYLINE, "yes"),
    ]
    out = ar.resolve_authors(["105"], engine=_engine_returning(rows))
    assert "105" in out
    # empty pid is emitted as-is (Author allows it; SPS shows name only).
    assert out["105"].first_person_identifier == ""
    assert out["105"].last_person_identifier == "fac_last"


def test_faculty_coauthor_preferred_for_displayed_pid():
    """When a position has a non-faculty AND a faculty row, show the faculty pid
    so the SPS headshot join lands on the faculty member (order-invariant)."""
    rows = [
        ("106", "ext_cofirst", "first", BYLINE, "no"),
        ("106", "fac_cofirst", "first", BYLINE, "yes"),  # faculty co-first
        ("106", "ext_last", "last", BYLINE, None),
    ]
    out = ar.resolve_authors(["106"], engine=_engine_returning(rows))
    assert "106" in out
    assert out["106"].first_person_identifier == "fac_cofirst"


def test_faculty_pid_preference_is_order_invariant():
    """Mirror of test_faculty_coauthor_preferred...: the faculty row appearing
    FIRST in result order must still win the displayed pid — this locks the
    first-faculty-wins guard, not just the SQL ORDER BY."""
    rows = [
        ("108", "fac_cofirst", "first", BYLINE, "yes"),  # faculty row first
        ("108", "ext_cofirst", "first", BYLINE, "no"),
        ("108", "ext_last", "last", BYLINE, None),
    ]
    out = ar.resolve_authors(["108"], engine=_engine_returning(rows))
    assert out["108"].first_person_identifier == "fac_cofirst"


def test_first_only_faculty_row_qualifies_with_empty_last():
    """A paper with ONLY a faculty first row (no last row at all) qualifies and
    emits an empty last pid via the AuthorPair default — a distinct empty-pid
    emission path from the external-co-lead case."""
    rows = [("300", "fac_first", "first", BYLINE, "yes")]
    out = ar.resolve_authors(["300"], engine=_engine_returning(rows))
    assert "300" in out
    assert out["300"].first_person_identifier == "fac_first"
    assert out["300"].last_person_identifier == ""


def test_warns_on_degraded_read_under_or_rule(caplog):
    """The #224 degraded-read WARN must fire on the OR path too (all-skipped ->
    0 resolved -> ratio 0 < floor), not only on the strict-AND path."""
    rows = [
        ("400", "ext_first", "first", BYLINE, None),
        ("400", "ext_last", "last", BYLINE, None),
    ]
    with caplog.at_level(logging.WARNING):
        ar.resolve_authors(["400"], engine=_engine_returning(rows))
    assert any(
        "degraded analysis_summary_author" in r.getMessage() for r in caplog.records
    )


def test_fulltime_faculty_value_is_case_and_space_insensitive():
    rows = [
        ("107", "fac_first", "first", BYLINE, " Yes "),
        ("107", "ext_last", "last", BYLINE, None),
    ]
    out = ar.resolve_authors(["107"], engine=_engine_returning(rows))
    assert "107" in out


def test_pmid_with_no_rows_is_absent():
    out = ar.resolve_authors(["999"], engine=_engine_returning([]))
    assert out == {}


def test_flag_off_default_uses_strict_and_and_suppresses_unlock(monkeypatch):
    """Default (flag off) keeps the legacy strict-AND rule: a both-pid paper
    resolves, but a faculty-last + empty-first paper (an OR-unlock candidate)
    does NOT — the publish-safe default that keeps every emitted pid non-empty."""
    monkeypatch.setattr(ar, "_faculty_or_enabled", lambda: False)
    cap = {}
    rows = [
        ("200", "wcm_first", "first", BYLINE, "yes"),
        ("200", "wcm_last", "last", BYLINE, "no"),
        # OR-unlock candidate: faculty last, external (empty-pid) first.
        ("201", "", "first", BYLINE, None),
        ("201", "fac_last", "last", BYLINE, "yes"),
    ]
    out = ar.resolve_authors(
        ["200", "201"], engine=_engine_returning(rows, capture=cap)
    )
    assert "200" in out  # both pids present -> strict-AND pass
    assert "201" not in out  # empty first pid -> unlock stays OFF by default
    # legacy SQL: no identity join under the default rule.
    sql = cap["sql"].lower()
    assert "left join" not in sql
    assert "fulltimefaculty" not in sql.replace(" ", "")
