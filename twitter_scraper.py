"""Twitter/X scraper for PM hiring signals in Bangalore using Apify."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import anthropic
import requests

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
MIN_VIEWS = 500      # proxy for account quality; maximedupre/twitter-scraper doesn't return follower counts
MIN_TWEET_SCORE = 3

# Queries that are already scoped to Bangalore — no post-filter needed
EXACT_QUERIES = [
    "hiring product manager Bangalore",
    "PM hiring Bangalore",
    "looking for PM Bangalore",
    "product manager opening Bengaluru",
    "we're hiring PM Bangalore",
]

# Broader queries — keep only tweets mentioning Bangalore/Bengaluru
BROAD_QUERIES = [
    "hiring product manager India",
    "PM opening India",
]

BANGALORE_RE = re.compile(r"\b(bangalore|bengaluru)\b", re.IGNORECASE)

_TWEET_SCORE_PROMPT = """\
Score this tweet 1-5 as a PM hiring signal for a candidate with this background:
- Eng-to-PM, 4.5 yrs eng + 2 yrs PM, based in Bangalore
- Wants early-stage Indian startup (0→1, Series A/B), consumer/social/fintech/B2B SaaS

Rules:
- NOT a real hiring signal (promo content, generic advice, news article)? → score 0
- Real hiring signal? Base score = 1
- Early-stage Indian startup vibe? +2
- Mentions eng background valued, 0→1, founding team, or scrappy culture? +2

Tweet:
{tweet_text}

Return JSON only: {{"score": <integer 0-5>, "reason": "<one-line reason>"}}"""


def _apify_run(apify_key: str, actor_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Run an Apify actor synchronously and return dataset items."""
    url = f"https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"
    resp = requests.post(
        url,
        params={"token": apify_key},
        json=payload,
        timeout=300,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("results") or data.get("items") or data.get("data") or []
    return []


def _normalize_tweet(item: dict[str, Any]) -> dict[str, Any] | None:
    """Normalise a raw maximedupre/twitter-scraper item. Returns None if skipped."""
    # maximedupre/twitter-scraper output fields
    text = (item.get("postText") or "").strip()
    if not text:
        return None

    # shouldIncludeReposts=False in the query, but guard anyway
    if item.get("nbReposts") is None and text.startswith("RT "):
        return None

    handle = (item.get("authorHandle") or "").lstrip("@")
    name = item.get("authorDisplayName") or handle
    tweet_url = item.get("postUrl") or ""
    if not tweet_url:
        post_id = item.get("postId") or ""
        if post_id and handle:
            tweet_url = f"https://x.com/{handle}/status/{post_id}"

    created_at = item.get("postDateTime") or "Unknown"

    # Use view count as a rough quality signal in place of follower count
    # (maximedupre/twitter-scraper does not return follower counts)
    nb_views = int(item.get("nbViews") or 0)
    if nb_views < MIN_VIEWS:
        return None

    return {
        "text": text,
        "handle": handle,
        "name": name,
        "followers": nb_views,   # stored as proxy; labelled "views" in email
        "url": tweet_url,
        "created_at": str(created_at),
        "nb_views": nb_views,
    }


def _score_tweet(text: str, client: anthropic.Anthropic) -> tuple[int, str]:
    """Score a single tweet 0–5. Returns (score, reason)."""
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=120,
            temperature=0,
            messages=[{
                "role": "user",
                "content": _TWEET_SCORE_PROMPT.format(tweet_text=text[:500]),
            }],
        )
        raw = (msg.content[0].text if msg.content else "").strip()
        # Parse JSON — handle both bare JSON and text-wrapped JSON
        if not raw.startswith("{"):
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            raw = m.group(0) if m else "{}"
        parsed = json.loads(raw)
        return max(0, min(5, int(parsed.get("score", 0)))), str(parsed.get("reason", ""))
    except Exception as exc:
        logger.warning("Tweet scoring failed: %s", exc)
        return 0, ""


def _fetch_tweets(apify_key: str, query: str, max_items: int) -> list[dict[str, Any]]:
    """Fetch tweets for one search query. Returns normalised list."""
    yesterday = (datetime.now(IST) - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        items = _apify_run(apify_key, "maximedupre~twitter-scraper", {
            "includePhrase": query,
            "shouldIncludeReposts": False,
            "shouldIncludeReplies": True,
            "searchMode": "latest",
            "startDate": yesterday,
            "maxNbItemsToScrape": max_items,
        })
        normalised = []
        for item in items:
            t = _normalize_tweet(item)
            if t:
                normalised.append(t)
        logger.info("Twitter '%s': %d/%d usable tweets", query, len(normalised), len(items))
        return normalised
    except Exception as exc:
        logger.warning("Twitter query '%s' failed: %s", query, exc)
        return []


def scrape(apify_key: str = "") -> list[dict[str, Any]]:
    """
    Scrape Twitter for PM hiring signals in Bangalore.
    Returns a list of signal dicts (NOT regular job dicts — kept separate for email rendering).
    Each dict has: tweet_text, author_handle, author_name, follower_count,
                   url, created_at, tweet_score, reason.
    """
    if not apify_key:
        logger.warning("APIFY_API_KEY not set; skipping Twitter scraper")
        return []

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set; skipping Twitter scraper")
        return []

    client = anthropic.Anthropic(api_key=api_key)

    raw: list[dict[str, Any]] = []

    for query in EXACT_QUERIES:
        raw.extend(_fetch_tweets(apify_key, query, max_items=20))

    for query in BROAD_QUERIES:
        tweets = _fetch_tweets(apify_key, query, max_items=30)
        for t in tweets:
            if BANGALORE_RE.search(t["text"]):
                raw.append(t)

    # Deduplicate by URL
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for t in raw:
        key = t["url"] or t["text"][:80]
        if key not in seen:
            seen.add(key)
            unique.append(t)

    # Score and filter
    signals: list[dict[str, Any]] = []
    for tweet in unique:
        score, reason = _score_tweet(tweet["text"], client)
        if score < MIN_TWEET_SCORE:
            continue
        signals.append({
            "tweet_text": tweet["text"],
            "author_handle": tweet["handle"],
            "author_name": tweet["name"],
            "follower_count": tweet["followers"],
            "url": tweet["url"],
            "created_at": tweet["created_at"],
            "tweet_score": score,
            "reason": reason,
        })

    # Sort best signals first
    signals.sort(key=lambda s: s["tweet_score"], reverse=True)
    logger.info(
        "Twitter scraper: %d signals scored >= %d (from %d unique tweets)",
        len(signals), MIN_TWEET_SCORE, len(unique),
    )
    return signals
