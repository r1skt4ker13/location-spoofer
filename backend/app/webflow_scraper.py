"""
Scraper for intern-list.com's server-rendered (Webflow CMS) category lists,
e.g. /pm-intern-list and /da-intern-list. List cards only carry title/company/
date, so details (location, qualifications, apply link) come from each job's
detail page, which is fetched only for ids we haven't seen before.
"""
import html
import logging
import re
import time
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urljoin

logger = logging.getLogger("webflow_scraper")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
MAX_PAGES = 3
STOP_AFTER_KNOWN = 25

_CARD_RE = re.compile(r'<div role="listitem" class="[^"]*w-dyn-item[^"]*">(.*?)</a></div>', re.S)
_HREF_RE = re.compile(r'href="([^"]+)"')
_TITLE_RE = re.compile(r'<p class="jobtitle">(.*?)</p>', re.S)
_DATE_RE = re.compile(r'<p class="blogtag">(.*?)</p>', re.S)
_COMPANY_RE = re.compile(r'<p class="companyname_list">(.*?)</p>', re.S)
_ID_RE = re.compile(r"_(\d+)/?$")
_PAGE_PARAM_RE = re.compile(r'href="\?([a-z0-9]+_page)=\d+"')

_APPLY_RE = re.compile(r'<a href="([^"]+)" class="link-2">Apply Now</a>')
_CLOSED_RE = re.compile(r'<div class="div-block-147(?P<cls>[^"]*)">')
_TAG_RE = re.compile(r'<div class="text-block-47">(.*?)</div>', re.S)
_QUAL_RE = re.compile(r'<h2 class="heading-30">Qualification</h2>(.*?)(?:<h2 |$)', re.S)
_DETAIL_COMPANY_RE = re.compile(r'<div class="text-block-42">(.*?)</div>', re.S)

WORK_MODELS = ("remote", "hybrid", "onsite", "on-site", "in-person")


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _text(fragment: str) -> str:
    fragment = re.sub(r"<li[^>]*>", "\n- ", fragment)
    fragment = re.sub(r"<(br|/p|/h\d|/div)[^>]*>", "\n", fragment)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    fragment = html.unescape(fragment)
    return re.sub(r"\n\s*\n+", "\n", fragment).strip()


def _parse_cards(page_html: str, base_url: str) -> list[dict]:
    cards = []
    for m in _CARD_RE.finditer(page_html):
        block = m.group(1)
        href = _HREF_RE.search(block)
        title = _TITLE_RE.search(block)
        if not href or not title:
            continue
        url = urljoin(base_url, href.group(1))
        id_m = _ID_RE.search(url)
        date = _DATE_RE.search(block)
        company = _COMPANY_RE.search(block)
        cards.append({
            "source_id": id_m.group(1) if id_m else url.rsplit("/", 1)[-1],
            "detail_url": url,
            "title": _text(title.group(1)),
            "date_posted": _text(date.group(1)) if date else None,
            "company": _text(company.group(1)) if company else None,
        })
    return cards


def _parse_detail(page_html: str) -> dict:
    out: dict = {}
    apply = _APPLY_RE.search(page_html)
    out["apply_url"] = html.unescape(apply.group(1)) if apply else None
    closed = _CLOSED_RE.search(page_html)
    # The "closed" banner carries w-condition-invisible when the job is still open.
    out["closed"] = bool(closed and "w-condition-invisible" not in closed.group("cls"))
    company = _DETAIL_COMPANY_RE.search(page_html)
    if company:
        out["company"] = _text(company.group(1))

    tags = [_text(t) for t in _TAG_RE.findall(page_html)]
    if tags:
        out["location"] = tags[0]
        for t in tags[1:]:
            low = t.lower()
            if "$" in t and not out.get("salary"):
                out["salary"] = t
            elif low in WORK_MODELS and not out.get("work_model"):
                out["work_model"] = t

    qual = _QUAL_RE.search(page_html)
    if qual:
        out["qualifications"] = _text(qual.group(1))[:4000]
    return out


def scrape(source: dict, known_ids: set[str], max_rows: int = 250) -> list[dict]:
    """Return new (unseen, still-open) jobs from one Webflow list source."""
    base_url = source["url"]
    prefix = source["key"]
    jobs: list[dict] = []
    seen_cards = 0
    consecutive_known = 0
    page_param = None

    for page_num in range(1, MAX_PAGES + 1):
        url = base_url if page_num == 1 else f"{base_url}?{page_param}={page_num}"
        try:
            page_html = _fetch(url)
        except Exception:
            logger.exception("failed to fetch list page %s", url)
            break
        if page_param is None:
            m = _PAGE_PARAM_RE.search(page_html)
            page_param = m.group(1) if m else None

        cards = _parse_cards(page_html, base_url)
        if not cards:
            break

        stop = False
        for card in cards:
            seen_cards += 1
            job_id = f"{prefix}:{card['source_id']}"
            if job_id in known_ids:
                consecutive_known += 1
                if consecutive_known >= STOP_AFTER_KNOWN:
                    stop = True
                    break
                continue
            consecutive_known = 0
            try:
                detail = _parse_detail(_fetch(card["detail_url"]))
            except Exception:
                logger.exception("failed to fetch detail %s", card["detail_url"])
                continue
            time.sleep(0.3)  # be polite
            if detail.pop("closed", False):
                continue
            job = {
                "id": job_id,
                "title": card["title"],
                "company": card["company"],
                "date_posted": card["date_posted"],
                **{k: v for k, v in detail.items() if v},
            }
            job.setdefault("apply_url", card["detail_url"])
            jobs.append(job)
            if seen_cards >= max_rows:
                stop = True
                break
        if stop or page_param is None:
            break

    now = datetime.now(timezone.utc).isoformat()
    for job in jobs:
        job["first_seen_at"] = now
    return jobs
