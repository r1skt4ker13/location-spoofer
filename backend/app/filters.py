"""
Deterministic eligibility filters, applied before any Claude call.

Location: Bay Area, NYC metro (incl. all of New Jersey), or remote.
GPA/transcript: drop postings whose text mentions either.
Degree: drop titles that signal MS/PhD-only or research roles. Claude's scoring
call does a second, fuller degree check against the qualifications text.
"""
import re

BAY_AREA_CITIES = [
    "san francisco", "south san francisco", "san jose", "santa clara", "palo alto",
    "mountain view", "sunnyvale", "menlo park", "redwood city", "redwood shores",
    "cupertino", "oakland", "berkeley", "fremont", "milpitas", "san mateo",
    "foster city", "burlingame", "san bruno", "emeryville", "hayward", "pleasanton",
    "livermore", "los gatos", "campbell", "walnut creek", "san ramon", "los altos",
    "san carlos", "belmont", "brisbane", "millbrae", "alameda", "richmond, ca",
    "concord, ca", "newark, ca", "dublin, ca", "union city", "san leandro",
    "daly city", "half moon bay", "saratoga", "morgan hill", "sausalito",
    "san rafael", "novato", "bay area", "silicon valley",
]
_BAY_RE = re.compile(r"\b(" + "|".join(re.escape(c) for c in BAY_AREA_CITIES) + r")\b|\bsf\b")

NYC_METRO = [
    "new york city", "nyc", "manhattan", "brooklyn", "queens", "bronx",
    "staten island", "long island city", "white plains", "purchase", "armonk",
    "tarrytown", "yonkers", "rye brook", "harrison, ny", "new rochelle",
    "jericho", "melville", "garden city", "great neck",
]
_NYC_RE = re.compile(r"\b(" + "|".join(re.escape(c) for c in NYC_METRO) + r")\b")
# "New York, NY" / "New York, New York" / bare "New York" = the city.
_NY_CITY_RE = re.compile(r"\bnew york\b(?!\s+state)")
_NJ_RE = re.compile(r",\s*nj\b|\bnew jersey\b|\bnj\b")
_REMOTE_RE = re.compile(r"\bremote\b")

_SPLIT_RE = re.compile(r"[;|\n]| / |\s+or\s+|\s+and\s+")


def _part_ok(part: str) -> bool:
    p = part.strip().lower()
    if not p:
        return False
    if _REMOTE_RE.search(p):
        return True
    if _BAY_RE.search(p) or _NJ_RE.search(p) or _NYC_RE.search(p):
        return True
    # "New York, NY" counts; "Rochester, NY" / "Albany, New York" do not.
    if _NY_CITY_RE.search(p):
        before = p[: _NY_CITY_RE.search(p).start()].strip(" ,")
        return before == ""
    return False


def location_ok(location: str | None, work_model: str | None = None) -> bool:
    if work_model and _REMOTE_RE.search(work_model.lower()):
        return True
    if not location:
        return False
    return any(_part_ok(p) for p in _SPLIT_RE.split(location))


_TITLE_BLOCK_RE = re.compile(
    r"\bresearch|\bph\.?\s?d\b|\bdoctoral\b|\bdoctorate\b|\bpost-?doc|"
    r"\bmaster'?s\b|\bms student\b|\bm\.s\.\b|\bmba\b|\bgraduate (intern|program|student)",
    re.I,
)


def degree_title_ok(title: str | None) -> bool:
    return not (title and _TITLE_BLOCK_RE.search(title))


_GPA_TRANSCRIPT_RE = re.compile(r"\bgpa\b|grade point average|\btranscripts?\b", re.I)


def gpa_transcript_ok(job: dict) -> bool:
    text = " ".join(job.get(k) or "" for k in ("title", "qualifications"))
    return not _GPA_TRANSCRIPT_RE.search(text)


def filter_reason(job: dict) -> str | None:
    """Return why a job is ineligible, or None if it passes."""
    if not degree_title_ok(job.get("title")):
        return "title indicates research or MS/PhD/MBA role"
    if not location_ok(job.get("location"), job.get("work_model")):
        return f"location not Bay Area / NYC-NJ / remote: {job.get('location') or 'unknown'}"
    if not gpa_transcript_ok(job):
        return "asks for GPA or transcript"
    return None
