"""#224: shrink_guard publish gate."""
from gates.shrink_guard import shrink_guard_gate


def test_first_publish_passes():
    r = shrink_guard_gate(prev_subtopic_count=None, new_subtopic_count=100)
    assert r.passed and not r.blocked


def test_prev_zero_passes():
    r = shrink_guard_gate(prev_subtopic_count=0, new_subtopic_count=100)
    assert r.passed


def test_growth_passes():
    r = shrink_guard_gate(
        prev_subtopic_count=100, new_subtopic_count=120, max_shrink_fraction=0.20
    )
    assert r.passed


def test_small_shrink_within_tolerance_passes():
    r = shrink_guard_gate(
        prev_subtopic_count=100, new_subtopic_count=85, max_shrink_fraction=0.20
    )
    assert r.passed  # 85 >= 80


def test_shrink_beyond_fraction_blocks():
    r = shrink_guard_gate(
        prev_subtopic_count=100, new_subtopic_count=70, max_shrink_fraction=0.20
    )
    assert not r.passed
    assert r.blocked
    assert r.details["prev"] == 100 and r.details["new"] == 70
    assert r.details["observed_shrink_fraction"] == 0.30


def test_injected_fraction_honored():
    # 30% shrink, but 40% allowed -> pass
    r = shrink_guard_gate(
        prev_subtopic_count=100, new_subtopic_count=70, max_shrink_fraction=0.40
    )
    assert r.passed
