"""Two-stage denoise: Stage A = deterministic regex/rules gate (this file, regex_gate).
Stage B (LLM judge) is added in a later task."""
import re

from pipeline_grants.models import Opportunity
from utils.iso_clock import now_iso

# Non-research opportunity types we never surface (matched against title).
_EXCLUDE_TYPE_RE = re.compile(
    r"\b(travel|conference|symposium|workshop|registration|poster|"
    r"prize|medal|lectureship|equipment|instrumentation|membership|"
    r"subscription|publication fee|page charge|open access fee)\b",
    re.IGNORECASE,
)


def regex_gate(opp: Opportunity):
    """Return (kept, reason). reason is '' when kept."""
    if _EXCLUDE_TYPE_RE.search(opp.title or ""):
        return False, "excluded type (regex)"
    if opp.due_date and opp.status != "continuous":
        # ISO dates compare lexicographically; now_iso() is "YYYY-MM-DD...Z".
        if opp.due_date < now_iso()[:10]:
            return False, "expired deadline"
    return True, ""
