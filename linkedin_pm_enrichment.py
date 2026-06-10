"""
Enrich high-scoring jobs with active PM contacts at the company via Apify LinkedIn scrapers.

Cost constraint: only jobs scoring >= 8 trigger Apify calls.
Results are cached in SQLite for 7 days per company to avoid repeat calls.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
CACHE_DB_PATH = Path(os.getenv("LINKEDIN_CACHE_DB_PATH", "linkedin_pm_cache.db"))
CACHE_TTL_DAYS = 7
MIN_SCORE_FOR_ENRICHMENT = 8

PM_TITLE_KEYWORDS = [
    "product manager",
    "senior pm",
    "head of product",
    "cpo",
    "vp product",
    "vp of product",
    "chief product officer",
    "director of product",
]


# ──────────────────────────────────────────────────────────────────────────────
# Cache helpers
# ──────────────────────────────────────────────────────────────────────────────

def _init_cache(db_path: Path = CACHE_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pm_contacts_cache (
            company_name TEXT PRIMARY KEY,
            contacts_json TEXT NOT NULL,
            cached_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _get_cached(company_name: str, conn: sqlite3.Connection) -> list[dict[str, Any]] | None:
    row = conn.execute(
        "SELECT contacts_json, cached_at FROM pm_contacts_cache WHERE company_name = ?",
        (company_name.lower().strip(),),
    ).fetchone()
    if not row:
        return None
    contacts_json, cached_at_str = row
    try:
        cached_dt = datetime.fromisoformat(cached_at_str)
        if cached_dt.tzinfo is None:
            cached_dt = cached_dt.replace(tzinfo=IST)
        age = datetime.now(IST) - cached_dt
        if age < timedelta(days=CACHE_TTL_DAYS):
            return json.loads(contacts_json)
    except (ValueError, TypeError, json.JSONDecodeError):
        pass
    return None


def _set_cache(company_name: str, contacts: list[dict[str, Any]], conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO pm_contacts_cache (company_name, contacts_json, cached_at)
        VALUES (?, ?, ?)
        """,
        (company_name.lower().strip(), json.dumps(contacts), datetime.now(IST).isoformat()),
    )
    conn.commit()


# ──────────────────────────────────────────────────────────────────────────────
# Apify helpers
# ──────────────────────────────────────────────────────────────────────────────

def _apify_run(apify_key: str, actor_id: str, payload: dict[str, Any], timeout: int = 300) -> list[dict[str, Any]]:
    url = f"https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"
    resp = requests.post(
        url,
        params={"token": apify_key},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("results") or data.get("employees") or data.get("items") or data.get("data") or []
    return []


def _company_slug(name: str) -> str:
    """Best-effort LinkedIn company slug from a company name."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def _is_pm_title(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in PM_TITLE_KEYWORDS)


# ──────────────────────────────────────────────────────────────────────────────
# Core enrichment
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_pm_contacts(company_name: str, apify_key: str) -> list[dict[str, Any]]:
    """
    Fetch up to 2 most recently active PM contacts at the company via Apify.
    Returns [] on any failure — never raises.
    """
    slug = _company_slug(company_name)
    company_url = f"https://www.linkedin.com/company/{slug}/"

    # Step 1: scrape company employees
    try:
        employees = _apify_run(apify_key, "apify/linkedin-company-scraper", {
            "startUrls": [{"url": company_url}],
            "maxResults": 50,
        })
    except Exception as exc:
        logger.warning("LinkedIn company scraper failed for %s: %s", company_name, exc)
        return []

    # Filter for PM-titled employees
    pm_employees = [
        emp for emp in employees
        if _is_pm_title(
            emp.get("title") or emp.get("headline") or emp.get("jobTitle") or ""
        )
    ]

    if not pm_employees:
        logger.info("No PM employees found at %s (tried %s)", company_name, company_url)
        return []

    # Collect LinkedIn profile URLs (limit to 5 to protect free credits)
    profile_urls = []
    for emp in pm_employees[:5]:
        url = (
            emp.get("linkedinUrl")
            or emp.get("profileUrl")
            or emp.get("url")
            or ""
        )
        if url and "linkedin.com/in/" in url:
            profile_urls.append(url)

    if not profile_urls:
        return []

    # Step 2: scrape profiles for recent post activity
    try:
        profiles = _apify_run(apify_key, "apify/linkedin-profile-scraper", {
            "profileUrls": profile_urls,
        })
    except Exception as exc:
        logger.warning("LinkedIn profile scraper failed for %s: %s", company_name, exc)
        return []

    cutoff = datetime.now(IST) - timedelta(days=30)
    contacts: list[dict[str, Any]] = []

    for profile in profiles:
        full_name = profile.get("fullName") or profile.get("name") or ""
        linkedin_url = profile.get("linkedinUrl") or profile.get("url") or ""
        posts = profile.get("posts") or profile.get("activity") or []

        recent: list[tuple[datetime, str]] = []
        for post in posts:
            date_str = (
                post.get("date") or post.get("publishedAt") or post.get("createdAt") or ""
            )
            try:
                dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=IST)
                if dt >= cutoff:
                    text = post.get("text") or post.get("content") or ""
                    recent.append((dt, text))
            except (ValueError, TypeError):
                continue

        if not recent:
            continue

        recent.sort(key=lambda x: x[0], reverse=True)
        latest_dt, latest_text = recent[0]

        days_ago = (datetime.now(IST) - latest_dt.replace(tzinfo=latest_dt.tzinfo or IST)).days
        day_label = f"{days_ago} day{'s' if days_ago != 1 else ''} ago"
        snippet = latest_text[:80].strip()
        outreach_reason = f"Posted {day_label}: {snippet}" if snippet else f"Posted {day_label}"

        contacts.append({
            "full_name": full_name,
            "linkedin_url": linkedin_url,
            "last_post_snippet": latest_text[:120],
            "last_post_date": latest_dt.isoformat(),
            "outreach_reason": outreach_reason,
            "post_count_30d": len(recent),
        })

    # Sort: most recent post first, then by frequency
    contacts.sort(key=lambda c: (c["last_post_date"], c["post_count_30d"]), reverse=True)
    return contacts[:2]


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def enrich(jobs: list[dict[str, Any]], apify_key: str = "") -> None:
    """
    Enrich jobs in-place by adding a "pm_contacts" key to each.

    - Jobs scoring < 8: pm_contacts = []  (no Apify calls made)
    - Jobs scoring >= 8: pm_contacts = [up to 2 active PM contact dicts]
    - Results cached per company for 7 days in linkedin_pm_cache.db
    """
    if not apify_key:
        logger.warning("APIFY_API_KEY not set; skipping LinkedIn enrichment")
        for job in jobs:
            job["pm_contacts"] = []
        return

    conn = _init_cache()

    for job in jobs:
        score = job.get("score", 0)
        company = (job.get("company") or "").strip()

        if score < MIN_SCORE_FOR_ENRICHMENT or not company or company == "Unknown":
            job["pm_contacts"] = []
            continue

        cached = _get_cached(company, conn)
        if cached is not None:
            logger.info("LinkedIn cache hit for '%s' (%d contacts)", company, len(cached))
            job["pm_contacts"] = cached
            continue

        logger.info("Fetching LinkedIn PM contacts for '%s' (score %d)", company, score)
        contacts = _fetch_pm_contacts(company, apify_key)
        _set_cache(company, contacts, conn)
        job["pm_contacts"] = contacts
        logger.info("Found %d active PM contacts at '%s'", len(contacts), company)
