"""LLM-based job scoring using Claude Haiku."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

CANDIDATE_PROFILE = """
Current role: Founding engineer transitioning to full-time PM
PM experience: ~2 years (product ownership at Port by Numberless - scaled messaging platform to 14K+ users; activation/retention work at Mosaic Wellness for a year)
Engineering experience: ~4.5 years (React Native, server-driven UI, PostgreSQL, real-time infra)
Location: Bangalore (open to remote)
Target: Early-stage startup PM roles (0→1, Series A/B), and mid stage companies, preferably consumer apps, social, fintech, or B2B SaaS
NOT looking for: Big tech/FAANG, pure engineering roles, roles requiring 5+ years explicit PM title
""".strip()

SCORING_RUBRIC = """
Score each job 1-10 against the candidate profile using these weights:
- Stage fit: early-stage startup = +3, Series A/B = +2, late stage = +1
- Domain fit: consumer/social/fintech/B2B SaaS = +2
- Background match: values eng-to-PM path or "founding" experience = +2
- Red flags: requires 5+ yrs PM title, FAANG-only culture signals = -3

Return JSON only:
{
  "score": <integer 1-10>,
  "reason": "<one-line reason>",
  "company_blurb": "<exactly two sentences about the company>"
}
""".strip()

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")
MIN_SCORE = int(os.getenv("MIN_JOB_SCORE", "6"))


def _build_prompt(job: dict[str, Any]) -> str:
    return f"""{SCORING_RUBRIC}

Candidate profile:
{CANDIDATE_PROFILE}

Job listing:
Title: {job.get('title', '')}
Company: {job.get('company', '')}
Location: {job.get('location', '')}
Experience required: {job.get('experience', '')}
Posted: {job.get('posted_at', '')}
Source: {job.get('source', '')}
Description:
{(job.get('description') or '')[:4000]}
"""


def _parse_response(text: str) -> dict[str, Any] | None:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def score_job(job: dict[str, Any], client: anthropic.Anthropic | None = None) -> dict[str, Any] | None:
    """Score a single job. Returns enriched job dict or None if below threshold."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set")
        return None

    client = client or anthropic.Anthropic(api_key=api_key)
    prompt = _build_prompt(job)

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=300,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text if message.content else ""
        parsed = _parse_response(raw)
        if not parsed:
            logger.warning("Could not parse scorer response for %s", job.get("title"))
            return None

        score = int(parsed.get("score", 0))
        reason = str(parsed.get("reason", "")).strip()
        company_blurb = str(parsed.get("company_blurb", "")).strip()

        if score < MIN_SCORE:
            return None

        enriched = dict(job)
        enriched["score"] = score
        enriched["reason"] = reason
        enriched["company_blurb"] = company_blurb
        return enriched
    except Exception as exc:
        logger.error("Scoring failed for %s: %s", job.get("title"), exc)
        return None


def score_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score all jobs, returning only those meeting the minimum threshold."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set; skipping scoring")
        return []

    client = anthropic.Anthropic(api_key=api_key)
    scored: list[dict[str, Any]] = []

    for job in jobs:
        result = score_job(job, client=client)
        if result:
            scored.append(result)

    scored.sort(key=lambda j: j.get("score", 0), reverse=True)
    logger.info("Scored %d/%d jobs above threshold %d", len(scored), len(jobs), MIN_SCORE)
    return scored
