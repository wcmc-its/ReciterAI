"""run.py --signal-report: per-family fires/decisive counts, re-scored through combine."""
import csv

from pipeline_cores import combine as _combine
from pipeline_cores.models import SignalResult
from pipeline_cores.signal_report import analyse, report

DISTINCTIVE_ACK = dict(ack_matched=True, ack_alias="ARCH", ack_alias_hits=10, ack_institution="home")


def _rec(pmid, **sig):
    return _combine.combine(pmid, "14", SignalResult(**sig))


def test_distinctive_ack_alone_is_the_sole_decisive_family():
    rec = _rec("1", **DISTINCTIVE_ACK)
    assert rec.status == "confirmed"
    assert analyse(rec, None) == {"fires": ["ack"], "decisive": ["ack"]}


def test_staff_beside_ack_fires_but_does_not_decide():
    # Staff alone is held at candidate, so knocking out ack drops the status; knocking
    # out staff leaves the ack confirming on its own.
    rec = _rec("2", coauthor_cwids=["abc1001"], **DISTINCTIVE_ACK)
    assert rec.status == "confirmed"
    a = analyse(rec, None)
    assert a["fires"] == ["ack", "staff"]
    assert a["decisive"] == ["ack"]


def test_staff_only_candidate_is_decided_by_staff():
    rec = _rec("3", coauthor_cwids=["abc1001"])
    assert rec.status == "candidate"
    assert analyse(rec, None)["decisive"] == ["staff"]


def test_report_counts_and_csv_skip_below_threshold(tmp_path, capsys):
    recs = [_rec("1", **DISTINCTIVE_ACK), _rec("3", coauthor_cwids=["abc1001"]), _rec("4")]
    assert recs[2].status == "below_threshold"
    path = tmp_path / "r.csv"
    out = report(recs, [], path=str(path))
    assert out["14"]["_n"] == {"confirmed": 1, "candidate": 1}
    assert out["14"]["ack"]["confirmed.sole"] == 1
    assert out["14"]["staff"]["candidate.decisive"] == 1
    rows = list(csv.DictReader(path.open()))
    assert [r["pmid"] for r in rows] == ["1", "3"]
    assert "[14 ] confirmed=1 candidate=1" in capsys.readouterr().out
