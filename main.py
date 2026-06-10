"""Orchestrate scraping, filtering, scoring, deduplication, and email delivery."""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import linkedin_pm_enrichment
import twitter_scraper
from emailer import send_email
from filter import apply_hard_filters
from scorer import score_jobs
from scraper import scrape_all

IST = timezone(timedelta(hours=5, minutes=30))
DB_PATH = Path(os.getenv("JOBS_DB_PATH", "jobs_seen.db"))


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_jobs (
            url TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            first_seen_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def filter_unseen_jobs(jobs: list[dict], conn: sqlite3.Connection) -> list[dict]:
    unseen: list[dict] = []
    for job in jobs:
        url = job.get("url", "").strip()
        if not url:
            continue
        row = conn.execute("SELECT 1 FROM seen_jobs WHERE url = ?", (url,)).fetchone()
        if not row:
            unseen.append(job)
    return unseen


def mark_jobs_seen(jobs: list[dict], conn: sqlite3.Connection) -> None:
    now = datetime.now(IST).isoformat()
    for job in jobs:
        url = job.get("url", "").strip()
        if not url:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO seen_jobs (url, title, company, first_seen_at)
            VALUES (?, ?, ?, ?)
            """,
            (url, job.get("title", ""), job.get("company", ""), now),
        )
    conn.commit()


def run() -> int:
    setup_logging()
    logger = logging.getLogger("main")
    logger.info("Starting PM job alert run")

    apify_key = os.getenv("APIFY_API_KEY", "")
    conn = init_db()

    # ── Line 1: collect Twitter hiring signals (kept separate from job pipeline) ──
    twitter_signals = twitter_scraper.scrape(apify_key)
    logger.info("Twitter scraper: %d signals collected", len(twitter_signals))

    raw_jobs = scrape_all(apify_api_key=apify_key)
    logger.info("Scraped %d total jobs across sources", len(raw_jobs))

    filtered_jobs = apply_hard_filters(raw_jobs)
    logger.info("%d jobs passed hard filters", len(filtered_jobs))

    new_jobs = filter_unseen_jobs(filtered_jobs, conn)
    logger.info("%d jobs are new (not previously sent)", len(new_jobs))

    scored_jobs = score_jobs(new_jobs)
    logger.info("%d jobs scored >= threshold", len(scored_jobs))

    # ── Line 2: enrich jobs scoring >= 8 with active PM contacts (modifies in-place) ──
    linkedin_pm_enrichment.enrich(scored_jobs, apify_key=apify_key)

    # Mark all newly discovered jobs as seen so they are never re-scored or re-sent.
    mark_jobs_seen(new_jobs, conn)

    try:
        send_email(scored_jobs, twitter_signals=twitter_signals)
    except Exception as exc:
        logger.error("Email delivery failed: %s", exc)
        return 1

    logger.info("Run completed successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
