"""2026-10-31 alias audit: an NIH intramural core and a moderate alias at another
institution must not confirm a WCM core."""
from pipeline_cores.combine import combine
from pipeline_cores.dictionary import load_core
from pipeline_cores.models import SignalResult
from pipeline_cores.signals import _alias_pattern, classify_institution


def _inst(text, alias):
    m = _alias_pattern(alias).search(text)
    return classify_institution(text, m, load_core("11"))


def test_nih_institute_adjacent_to_the_alias_is_another_institution():
    a = "Microscopy and Imaging Core"
    assert _inst("We thank V. Schram from the NICHD Microscopy and Imaging Core for support.", a) == "other"
    assert _inst("Dr. Schram from the Microscopy and Imaging Core (NICHD) for support.", a) == "other"
    assert _inst("A confocal within the Microscopy and Imaging Core, NICHD, was used.", a) == "other"


def test_nih_institute_as_a_funder_is_not_an_institution():
    a = "Microscopy and Imaging Core"
    text = "Imaging used the Microscopy and Imaging Core. This work was supported by NICHD grant R01HD000000."
    assert _inst(text, a) == "none"
    assert _inst("We thank the Weill Cornell Medicine Microscopy and Imaging Core (NICHD R01).", a) == "home"


def test_moderate_alias_at_another_institution_is_held_at_candidate():
    sig = SignalResult(ack_matched=True, ack_alias="Genomics Resources Core", ack_alias_hits=470,
                       ack_institution="other")
    rec = combine("1", "5", sig)
    assert rec.status == "candidate"
    assert rec.likelihood >= 0.65                    # still ranks at the top of the queue
    home = combine("1", "5", SignalResult(**{**sig.__dict__, "ack_institution": "home"}))
    assert home.status == "confirmed"


def test_unique_alias_still_confirms_next_to_another_institution():
    core = load_core("2")
    alias = "Citigroup Biomedical Imaging Center"
    assert alias in core.unique_aliases
    sig = SignalResult(ack_matched=True, ack_alias=alias, ack_alias_hits=core.alias_hits.get(alias),
                       ack_institution="other")
    assert combine("1", "2", sig, core=core).status == "confirmed"
    generic = SignalResult(ack_matched=True, ack_alias="Biomedical Imaging Core", ack_alias_hits=209,
                           ack_institution="other")
    assert combine("1", "2", generic, core=core).status != "confirmed"


def test_pcori_is_a_funder_not_an_institution():
    text = ("We used data from the INSIGHT Clinical Research Network. INSIGHT is funded by the "
            "Patient-Centered Outcomes Research Institute (PCORI).")
    m = _alias_pattern("INSIGHT Clinical Research Network").search(text)
    assert classify_institution(text, m, load_core("14")) == "none"
