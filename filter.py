"""Hard filters for PM job listings."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))

PM_TITLE_PATTERN = re.compile(
    r"\b("
    r"product\s+manager|"
    r"associate\s+product\s+manager|"
    r"\bapm\b|"
    r"senior\s+product\s+manager|"
    r"group\s+product\s+manager|"
    r"head\s+of\s+product|"
    r"lead\s+product\s+manager|"
    r"principal\s+product\s+manager"
    r")\b",
    re.IGNORECASE,
)

EXCLUDED_TITLE_PATTERN = re.compile(
    r"\b("
    r"product\s+marketing|"
    r"program\s+manager|"
    r"project\s+manager|"
    r"technical\s+program\s+manager|"
    r"tpM\b|"
    r"product\s+operations\s+manager"
    r")\b",
    re.IGNORECASE,
)

TECHNICAL_PM_CODING_PATTERN = re.compile(
    r"\b("
    r"active\s+coding|"
    r"must\s+code|"
    r"hands[- ]on\s+coding|"
    r"write\s+production\s+code|"
    r"software\s+engineering\s+background\s+required"
    r")\b",
    re.IGNORECASE,
)

LOCATION_PATTERN = re.compile(
    r"\b("
    r"bangalore|bengaluru|"
    r"remote|work\s+from\s+home|wfh|"
    r"pan[- ]?india|anywhere\s+in\s+india|india"
    r")\b",
    re.IGNORECASE,
)

EXPERIENCE_PATTERN = re.compile(
    r"(\d+)\s*(?:\+|\+?\s*years?|yrs?|yr\b)",
    re.IGNORECASE,
)

EXPERIENCE_RANGE_PATTERN = re.compile(
    r"(\d+)\s*[-–to]+\s*(\d+)\s*(?:years?|yrs?|yr\b)?",
    re.IGNORECASE,
)


def _now_ist() -> datetime:
    return datetime.now(IST)


def is_pm_title(title: str) -> bool:
    if not title:
        return False
    if EXCLUDED_TITLE_PATTERN.search(title):
        return False
    return bool(PM_TITLE_PATTERN.search(title))


def is_excluded_role(title: str, description: str = "") -> bool:
    combined = f"{title} {description}"
    if EXCLUDED_TITLE_PATTERN.search(combined):
        return True
    if re.search(r"\btechnical\s+product\s+manager\b", combined, re.IGNORECASE):
        if TECHNICAL_PM_CODING_PATTERN.search(combined):
            return True
    return False


def parse_max_experience_years(text: str) -> int | None:
    if not text:
        return None
    lowered = text.lower()
    if any(token in lowered for token in ("fresher", "0 year", "0 yr", "entry level", "no experience")):
        return 0

    range_match = EXPERIENCE_RANGE_PATTERN.search(text)
    if range_match:
        return max(int(range_match.group(1)), int(range_match.group(2)))

    plus_match = re.search(r"(\d+)\s*\+\s*(?:years?|yrs?)", text, re.IGNORECASE)
    if plus_match:
        return int(plus_match.group(1))

    matches = EXPERIENCE_PATTERN.findall(text)
    if matches:
        return max(int(m) for m in matches)

    return None


def experience_within_limit(job: dict[str, Any], max_years: int = 6) -> bool:
    fields = " ".join(
        filter(
            None,
            [
                job.get("experience", ""),
                job.get("title", ""),
                job.get("description", ""),
            ],
        )
    )
    max_required = parse_max_experience_years(fields)
    if max_required is None:
        return True
    return max_required <= max_years


def is_valid_location(location: str, description: str = "") -> bool:
    combined = f"{location} {description}"
    return bool(LOCATION_PATTERN.search(combined))


def is_posted_within_hours(job: dict[str, Any], hours: int = 24) -> bool:
    posted_dt = job.get("posted_datetime")
    if isinstance(posted_dt, datetime):
        if posted_dt.tzinfo is None:
            posted_dt = posted_dt.replace(tzinfo=IST)
        return posted_dt >= _now_ist() - timedelta(hours=hours)

    posted_text = str(job.get("posted_at", "")).lower()
    if not posted_text or posted_text == "unknown":
        return True

    if any(token in posted_text for token in ("just now", "today", "hour", "minute", "few hours")):
        return True
    if "yesterday" in posted_text:
        return True

    day_match = re.search(r"(\d+)\s*days?\s*ago", posted_text)
    if day_match:
        return int(day_match.group(1)) <= 1

    if re.search(r"\b(week|month|weeks|months)\b", posted_text):
        return False

    try:
        parsed = datetime.fromisoformat(posted_text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        return parsed >= _now_ist() - timedelta(hours=hours)
    except ValueError:
        return True


def passes_hard_filters(job: dict[str, Any]) -> bool:
    title = job.get("title", "")
    description = job.get("description", "")
    location = job.get("location", "")

    if not job.get("url") or not title:
        return False
    if not is_pm_title(title):
        return False
    if is_excluded_role(title, description):
        return False
    if not experience_within_limit(job):
        return False
    if not is_valid_location(location, description):
        return False
    if not is_posted_within_hours(job):
        return False
    return True


def apply_hard_filters(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [job for job in jobs if passes_hard_filters(job)]
