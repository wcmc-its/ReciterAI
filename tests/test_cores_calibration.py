"""Per-core probability calibration hook (combine.calibrated_logit, dictionary `calibration:`)."""
import math
from dataclasses import replace

import pytest

from pipeline_cores import combine as C
from pipeline_cores.dictionary import load_cores
from pipeline_cores.models import (
    STATUS_BELOW,
    STATUS_CANDIDATE,
    STATUS_CONFIRMED,
    CoreDefinition,
    SignalResult,
)


def _core(**kw):
    return CoreDefinition(core_id="99", name="Test", aliases=["Test Core"], **kw)


def _logit(p):
    return math.log(p / (1 - p))


EVIDENCE = [
    SignalResult(),
    SignalResult(llm_score=9),
    SignalResult(coauthor_cwids=["x"]),
    SignalResult(author_affinity=0.2, llm_score=8),
    SignalResult(ack_matched=True, ack_alias_hits=20, ack_institution="home"),
]


def test_identity_by_default_scores_exactly_as_before():
    plain = _core()
    for s in EVIDENCE:
        raw = C.PRIOR_LOGIT + sum(w for _, w in C.explain(s))
        assert C.score(s) == pytest.approx(1 / (1 + math.exp(-raw)), abs=1e-12)
        assert C.score(s, plain) == C.score(s)
        assert C.combine("1", "99", s, core=plain).likelihood == round(C.score(s), 4)


def test_explicit_identity_is_a_no_op():
    core = _core(calibration_intercept=0.0, calibration_slope=1.0)
    for s in EVIDENCE:
        assert C.score(s, core) == pytest.approx(C.score(s), abs=1e-12)


def test_map_is_applied_to_the_logit():
    core = _core(calibration_intercept=-1.0, calibration_slope=0.5)
    for s in EVIDENCE:
        raw = _logit(C.score(s))
        assert _logit(C.score(s, core)) == pytest.approx(-1.0 + 0.5 * raw, abs=1e-9)


def test_half_specified_map_keeps_the_other_term_at_identity():
    s = SignalResult(llm_score=9)
    raw = _logit(C.score(s))
    assert _logit(C.score(s, _core(calibration_slope=0.5))) == pytest.approx(0.5 * raw)
    assert _logit(C.score(s, _core(calibration_intercept=-2.0))) == pytest.approx(raw - 2.0)


def test_positive_slope_preserves_the_ranking():
    core = _core(calibration_intercept=0.7, calibration_slope=0.3)
    raw = [C.score(s) for s in EVIDENCE]
    cal = [C.score(s, core) for s in EVIDENCE]
    assert sorted(range(len(raw)), key=raw.__getitem__) == sorted(range(len(cal)), key=cal.__getitem__)


def test_status_bands_on_the_calibrated_probability():
    ack = SignalResult(ack_matched=True, ack_alias_hits=20, ack_institution="home")
    assert C.combine("1", "99", ack, core=_core()).status == STATUS_CONFIRMED
    # A map that pulls everything far down demotes even a distinctive alias.
    low = _core(calibration_intercept=-20.0, calibration_slope=1.0)
    assert C.combine("1", "99", ack, core=low).status == STATUS_BELOW
    # ...and one that pushes a lone LLM score over the confirm bar is still held to
    # candidate by the never-the-deciding-vote rule, because the held score is
    # calibrated with the same map.
    high = _core(calibration_intercept=3.0, calibration_slope=1.0)
    rec = C.combine("1", "99", SignalResult(llm_score=9), core=high)
    assert rec.likelihood >= C.DEFAULT_CONFIRM_THRESHOLD
    assert rec.status == STATUS_CANDIDATE
    # The held score must be CALIBRATED too: aff:regular alone is 0.40 raw (would hold
    # aff:regular + llm at candidate) but clears the bar under this map, so the pair
    # confirms on the usage prior, not on the LLM.
    pair = SignalResult(author_affinity=0.2, llm_score=9)
    assert C.score(SignalResult(author_affinity=0.2)) < C.DEFAULT_CONFIRM_THRESHOLD
    assert C.combine("1", "99", pair, core=high).status == STATUS_CONFIRMED


def test_extreme_calibrated_logits_do_not_overflow():
    core = _core(calibration_intercept=-1000.0, calibration_slope=1.0)
    assert C.score(SignalResult(), core) == 0.0
    core = _core(calibration_intercept=1000.0, calibration_slope=1.0)
    assert C.score(SignalResult(), core) == 1.0


def _write(tmp_path, block: str):
    p = tmp_path / "d.yaml"
    p.write_text("cores:\n  - core_id: 1\n    name: X\n    aliases: [\"X Core\"]\n" + block)
    return p


def test_loader_reads_the_mapping(tmp_path):
    (c,) = load_cores(_write(tmp_path, "    calibration: {intercept: -0.5, slope: 0.8}\n"))
    assert (c.calibration_intercept, c.calibration_slope) == (-0.5, 0.8)


def test_loader_absent_key_is_identity(tmp_path):
    (c,) = load_cores(_write(tmp_path, ""))
    assert (c.calibration_intercept, c.calibration_slope) == (None, None)
    assert C.calibrated_logit(1.23, c) == 1.23


@pytest.mark.parametrize("block, msg", [
    ("    calibration: {intercept: 0, slope: 0}\n", "slope must be > 0"),
    ("    calibration: {slope: -1}\n", "slope must be > 0"),
    ("    calibration: {slop: 0.5}\n", "unknown key"),
    ("    calibration: 0.5\n", "must be a mapping"),
    ("    calibration: {intercept: .nan}\n", "must be finite"),
])
def test_loader_rejects_bad_maps(tmp_path, block, msg):
    with pytest.raises(ValueError, match=msg):
        load_cores(_write(tmp_path, block))


def test_shipped_dictionary_is_identity_everywhere():
    """2026-10-06: no core carries a calibration yet (core 14 was measured and left at
    identity; core_dictionary.yaml says why). Delete this test the day one is set."""
    for c in load_cores():
        assert c.calibration_intercept is None and c.calibration_slope is None, c.core_id


def test_replace_keeps_calibration():
    core = replace(_core(), calibration_slope=0.5)
    assert C.calibrated_logit(2.0, core) == 1.0
