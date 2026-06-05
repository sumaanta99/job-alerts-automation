"""Job scrapers for PM roles across Indian job boards."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/json",
    "Accept-Language": "en-IN,en;q=0.9",
}


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    session.trust_env = False
    return session


def _normalize_job(
    *,
    title: str,
    company: str,
    url: str,
    location: str,
    experience: str,
    posted_at: str,
    description: str,
    source: str,
    posted_datetime: datetime | None = None,
) -> dict[str, Any]:
    return {
        "title": title.strip(),
        "company": company.strip() or "Unknown",
        "url": url.strip(),
        "location": location.strip() or "Not specified",
        "experience": experience.strip() or "Not specified",
        "posted_at": posted_at.strip() or "Unknown",
        "posted_datetime": posted_datetime,
        "description": description.strip(),
        "source": source,
    }


def _parse_relative_posted(text: str, now: datetime | None = None) -> datetime | None:
    if not text:
        return None
    now = now or datetime.now(IST)
    lowered = text.lower().strip()
    if lowered in {"just now", "today", "few hours ago", "few hours"}:
        return now
    if "yesterday" in lowered:
        return now - timedelta(days=1)
    match = re.search(r"(\d+)\s*(minute|hour|day|week|month)s?\s*ago", lowered)
    if match:
        value = int(match.group(1))
        unit = match.group(2)
        if unit == "minute":
            return now - timedelta(minutes=value)
        if unit == "hour":
            return now - timedelta(hours=value)
        if unit == "day":
            return now - timedelta(days=value)
        if unit == "week":
            return now - timedelta(weeks=value)
        if unit == "month":
            return now - timedelta(days=value * 30)
    try:
        parsed = date_parser.parse(text, fuzzy=True)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        return parsed.astimezone(IST)
    except (ValueError, TypeError, OverflowError):
        return None


def _extract_json_ld_jobs(html: str, source: str) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") != "JobPosting":
                continue
            org = item.get("hiringOrganization") or {}
            company = org.get("name", "") if isinstance(org, dict) else str(org)
            location = ""
            job_location = item.get("jobLocation")
            if isinstance(job_location, dict):
                address = job_location.get("address") or {}
                if isinstance(address, dict):
                    location = address.get("addressLocality") or address.get("addressRegion") or ""
            posted_dt = _parse_relative_posted(item.get("datePosted", ""))
            jobs.append(
                _normalize_job(
                    title=item.get("title", ""),
                    company=company,
                    url=item.get("url", ""),
                    location=location,
                    experience="Not specified",
                    posted_at=item.get("datePosted", "Unknown"),
                    description=item.get("description", ""),
                    source=source,
                    posted_datetime=posted_dt,
                )
            )
    return jobs


def scrape_naukri() -> list[dict[str, Any]]:
    """Scrape Naukri PM jobs in Bangalore posted in last 24h."""
    source = "naukri"
    jobs: list[dict[str, Any]] = []
    session = _session()

    api_url = "https://www.naukri.com/jobapi/v3/search"
    params = {
        "noOfResults": 40,
        "urlType": "search_by_keyword",
        "searchType": "adv",
        "keyword": "product manager",
        "location": "bangalore",
        "pageNo": 1,
        "jobAge": 1,
        "sort": "f",
    }

    try:
        response = session.get(api_url, params=params, timeout=30)
        if response.status_code == 200:
            data = response.json()
            for item in data.get("jobDetails", []) or data.get("jobs", []) or []:
                title = item.get("title") or item.get("jobTitle") or ""
                company = item.get("companyName") or item.get("company") or ""
                job_id = item.get("jobId") or item.get("jdId") or ""
                url = item.get("jdURL") or item.get("jobUrl") or ""
                if not url and job_id:
                    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                    url = f"https://www.naukri.com/job-listings-{slug}-{job_id}"
                if url and not url.startswith("http"):
                    url = urljoin("https://www.naukri.com", url)
                location = item.get("location") or ""
                if not location and item.get("placeholders"):
                    location = item["placeholders"][0].get("label", "")
                if not location and item.get("locations"):
                    location = item["locations"][0].get("label", "")
                experience = item.get("experienceText") or item.get("minimumExperience", "")
                if experience and item.get("maximumExperience"):
                    experience = f"{experience}-{item.get('maximumExperience')} yrs"
                posted_raw = item.get("footerPlaceholderLabel") or item.get("createdDate") or item.get("jobAge", "")
                posted_dt = _parse_relative_posted(str(posted_raw))
                description = item.get("jobDescription") or item.get("description", "")
                if title and url:
                    jobs.append(
                        _normalize_job(
                            title=title,
                            company=company,
                            url=url,
                            location=str(location),
                            experience=str(experience),
                            posted_at=str(posted_raw),
                            description=description,
                            source=source,
                            posted_datetime=posted_dt,
                        )
                    )
            if jobs:
                logger.info("Naukri API returned %d jobs", len(jobs))
                return jobs
    except Exception as exc:
        logger.warning("Naukri API scrape failed: %s", exc)

    search_url = "https://www.naukri.com/product-manager-jobs-in-bangalore?jobAge=1"
    try:
        response = session.get(search_url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        cards = soup.select(
            "div.cust-job-tuple, article.jobTuple, div.tuple, .srp-jobtuple-wrapper"
        )
        for card in cards:
            title_el = card.select_one("a.title, h2 a, .title, a[title]")
            company_el = card.select_one(".comp-name, .companyInfo a, .comp-dtls-wrap a")
            exp_el = card.select_one(".expwdth, .experience, .job-exp")
            loc_el = card.select_one(".locWdth, .loc, .loc-wrap")
            posted_el = card.select_one(".job-post-day, .type, span.fleft")
            desc_el = card.select_one(".job-desc, .job-description")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            url = title_el.get("href", "")
            if url and not url.startswith("http"):
                url = urljoin("https://www.naukri.com", url)
            posted_raw = posted_el.get_text(strip=True) if posted_el else "Unknown"
            jobs.append(
                _normalize_job(
                    title=title,
                    company=company_el.get_text(strip=True) if company_el else "",
                    url=url,
                    location=loc_el.get_text(strip=True) if loc_el else "",
                    experience=exp_el.get_text(strip=True) if exp_el else "",
                    posted_at=posted_raw,
                    description=desc_el.get_text(strip=True) if desc_el else "",
                    source=source,
                    posted_datetime=_parse_relative_posted(posted_raw),
                )
            )
        jobs.extend(_extract_json_ld_jobs(response.text, source))
        logger.info("Naukri HTML returned %d jobs", len(jobs))
    except Exception as exc:
        logger.error("Naukri scrape failed: %s", exc)
        raise

    return _dedupe_by_url(jobs)


def scrape_instahyre() -> list[dict[str, Any]]:
    """Scrape Instahyre PM jobs."""
    source = "instahyre"
    jobs: list[dict[str, Any]] = []
    session = _session()
    search_url = "https://www.instahyre.com/search-jobs/?keywords=product+manager"

    try:
        response = session.get(search_url, timeout=30)
        response.raise_for_status()

        # Instahyre often embeds job data in script tags.
        match = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;", response.text, re.DOTALL)
        if match:
            try:
                state = json.loads(match.group(1))
                listings = (
                    state.get("search", {}).get("jobs", [])
                    or state.get("jobs", [])
                    or []
                )
                for item in listings:
                    job_id = item.get("id") or item.get("job_id")
                    slug = item.get("slug") or item.get("job_slug") or ""
                    url = item.get("url") or item.get("job_url") or ""
                    if not url and slug:
                        url = f"https://www.instahyre.com/{slug}-job/"
                    elif not url and job_id:
                        url = f"https://www.instahyre.com/job/{job_id}/"
                    posted_raw = item.get("posted_on") or item.get("created_at") or ""
                    posted_dt = _parse_relative_posted(str(posted_raw))
                    jobs.append(
                        _normalize_job(
                            title=item.get("title") or item.get("designation", ""),
                            company=item.get("company_name") or item.get("company", ""),
                            url=url,
                            location=item.get("location") or item.get("locations", ""),
                            experience=item.get("experience") or item.get("min_experience", ""),
                            posted_at=str(posted_raw or "Unknown"),
                            description=item.get("description") or item.get("job_description", ""),
                            source=source,
                            posted_datetime=posted_dt,
                        )
                    )
            except json.JSONDecodeError:
                pass

        soup = BeautifulSoup(response.text, "lxml")
        cards = soup.select(".job-card, .job-listing, li.search-result, div[data-job-id]")
        for card in cards:
            title_el = card.select_one("h2 a, h3 a, .job-title a, a.job-link")
            company_el = card.select_one(".company-name, .employer, .company")
            loc_el = card.select_one(".location, .job-location")
            exp_el = card.select_one(".experience, .job-experience")
            posted_el = card.select_one(".posted, .job-posted, time")
            if not title_el:
                continue
            url = title_el.get("href", "")
            if url and not url.startswith("http"):
                url = urljoin("https://www.instahyre.com", url)
            posted_raw = posted_el.get_text(strip=True) if posted_el else "Unknown"
            jobs.append(
                _normalize_job(
                    title=title_el.get_text(strip=True),
                    company=company_el.get_text(strip=True) if company_el else "",
                    url=url,
                    location=loc_el.get_text(strip=True) if loc_el else "",
                    experience=exp_el.get_text(strip=True) if exp_el else "",
                    posted_at=posted_raw,
                    description=card.get_text(" ", strip=True)[:2000],
                    source=source,
                    posted_datetime=_parse_relative_posted(posted_raw),
                )
            )

        jobs.extend(_extract_json_ld_jobs(response.text, source))
        logger.info("Instahyre returned %d jobs", len(jobs))
    except Exception as exc:
        logger.error("Instahyre scrape failed: %s", exc)
        raise

    return _dedupe_by_url(jobs)


def scrape_cutshort() -> list[dict[str, Any]]:
    """Scrape Cutshort PM jobs in Bangalore."""
    source = "cutshort"
    jobs: list[dict[str, Any]] = []
    session = _session()

    api_candidates = [
        (
            "https://cutshort.io/api/job/search",
            {
                "query": "product manager",
                "locations": "bangalore",
                "page": 1,
                "limit": 50,
            },
        ),
        (
            "https://cutshort.io/api/search/jobs",
            {"q": "product manager", "location": "bangalore"},
        ),
    ]

    for api_url, payload in api_candidates:
        try:
            response = session.post(api_url, json=payload, timeout=30)
            if response.status_code != 200:
                response = session.get(api_url, params=payload, timeout=30)
            if response.status_code == 200:
                data = response.json()
                listings = data if isinstance(data, list) else data.get("jobs") or data.get("data") or []
                for item in listings:
                    url = item.get("apply_url") or item.get("url") or item.get("job_url") or ""
                    if not url:
                        job_id = item.get("id") or item.get("_id") or ""
                        if job_id:
                            url = f"https://cutshort.io/job/{job_id}"
                    posted_raw = item.get("posted_at") or item.get("createdAt") or ""
                    posted_dt = _parse_relative_posted(str(posted_raw))
                    jobs.append(
                        _normalize_job(
                            title=item.get("title") or item.get("headline", ""),
                            company=item.get("company_name") or item.get("company", ""),
                            url=url,
                            location=item.get("location", "Bangalore"),
                            experience=item.get("experience_range") or item.get("experience", ""),
                            posted_at=str(posted_raw or "Unknown"),
                            description=item.get("description", ""),
                            source=source,
                            posted_datetime=posted_dt,
                        )
                    )
                if jobs:
                    logger.info("Cutshort API returned %d jobs", len(jobs))
                    return _dedupe_by_url(jobs)
        except Exception as exc:
            logger.warning("Cutshort API %s failed: %s", api_url, exc)

    search_url = "https://cutshort.io/jobs/product-manager-jobs-in-bangalore-bengaluru"
    try:
        response = session.get(search_url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")

        for link in soup.select('a[href*="/job/"]'):
            href = link.get("href", "")
            title_lower = link.get_text(strip=True).lower()
            if not href or (
                "product-manager" not in href.lower() and "product" not in title_lower
            ):
                continue
            url = href if href.startswith("http") else urljoin("https://cutshort.io", href)
            title = link.get_text(strip=True)
            if not title or len(title) < 5:
                continue
            parent = link.find_parent(["div", "article", "li"])
            company = ""
            location = "Bangalore"
            experience = ""
            posted_raw = "Unknown"
            if parent:
                company_el = parent.select_one(".company, .company-name, [class*='company']")
                loc_el = parent.select_one(".location, [class*='location']")
                exp_el = parent.select_one(".experience, [class*='experience']")
                posted_el = parent.select_one("time, .posted, [class*='posted']")
                company = company_el.get_text(strip=True) if company_el else ""
                location = loc_el.get_text(strip=True) if loc_el else location
                experience = exp_el.get_text(strip=True) if exp_el else ""
                posted_raw = posted_el.get_text(strip=True) if posted_el else posted_raw
            jobs.append(
                _normalize_job(
                    title=title,
                    company=company,
                    url=url,
                    location=location,
                    experience=experience,
                    posted_at=posted_raw,
                    description=parent.get_text(" ", strip=True)[:2000] if parent else "",
                    source=source,
                    posted_datetime=_parse_relative_posted(posted_raw),
                )
            )

        jobs.extend(_extract_json_ld_jobs(response.text, source))
        logger.info("Cutshort HTML returned %d jobs", len(jobs))
    except Exception as exc:
        logger.error("Cutshort scrape failed: %s", exc)
        raise

    return _dedupe_by_url(jobs)


def scrape_wellfound() -> list[dict[str, Any]]:
    """Scrape Wellfound (AngelList) PM jobs in India."""
    source = "wellfound"
    jobs: list[dict[str, Any]] = []
    session = _session()

    graphql_url = "https://wellfound.com/graphql"
    query = """
    query StartupJobs($filter: StartupJobSearchFilter!) {
      startupJobSearch(filter: $filter) {
        startups {
          name
          slug
          highConcept
          jobs {
            id
            title
            slug
            locationNames
            remote
            description
            createdAt
            experience
          }
        }
      }
    }
    """
    variables = {
        "filter": {
            "roleTagIds": [],
            "roleTitle": "product manager",
            "locations": ["India"],
            "remote": True,
        }
    }

    try:
        response = session.post(
            graphql_url,
            json={"query": query, "variables": variables},
            headers={**DEFAULT_HEADERS, "Content-Type": "application/json"},
            timeout=30,
        )
        if response.status_code == 200:
            payload = response.json()
            startups = (
                payload.get("data", {})
                .get("startupJobSearch", {})
                .get("startups", [])
            )
            for startup in startups or []:
                company = startup.get("name", "")
                blurb = startup.get("highConcept", "")
                slug = startup.get("slug", "")
                for job in startup.get("jobs", []) or []:
                    job_slug = job.get("slug") or job.get("id")
                    url = f"https://wellfound.com/company/{slug}/jobs/{job_slug}" if slug and job_slug else ""
                    locations = job.get("locationNames") or []
                    location = ", ".join(locations) if locations else ("Remote" if job.get("remote") else "India")
                    posted_raw = job.get("createdAt", "")
                    posted_dt = _parse_relative_posted(str(posted_raw))
                    description = (job.get("description") or "") + ("\n" + blurb if blurb else "")
                    jobs.append(
                        _normalize_job(
                            title=job.get("title", ""),
                            company=company,
                            url=url,
                            location=location,
                            experience=job.get("experience", "") or "Not specified",
                            posted_at=str(posted_raw or "Unknown"),
                            description=description,
                            source=source,
                            posted_datetime=posted_dt,
                        )
                    )
            if jobs:
                logger.info("Wellfound GraphQL returned %d jobs", len(jobs))
                return _dedupe_by_url(jobs)
    except Exception as exc:
        logger.warning("Wellfound GraphQL failed: %s", exc)

    search_url = "https://wellfound.com/role/l/product-manager/india"
    try:
        response = session.get(search_url, timeout=30)
        response.raise_for_status()

        next_data = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            response.text,
            re.DOTALL,
        )
        if next_data:
            try:
                data = json.loads(next_data.group(1))
                listings = (
                    data.get("props", {})
                    .get("pageProps", {})
                    .get("jobs", [])
                    or data.get("props", {}).get("pageProps", {}).get("listings", [])
                    or []
                )
                for item in listings:
                    startup = item.get("startup") or {}
                    company = startup.get("name") or item.get("company_name", "")
                    url = item.get("url") or item.get("job_url") or ""
                    if not url:
                        startup_slug = startup.get("slug", "")
                        job_slug = item.get("slug") or item.get("id")
                        if startup_slug and job_slug:
                            url = f"https://wellfound.com/company/{startup_slug}/jobs/{job_slug}"
                    posted_raw = item.get("created_at") or item.get("posted_at") or ""
                    posted_dt = _parse_relative_posted(str(posted_raw))
                    jobs.append(
                        _normalize_job(
                            title=item.get("title", ""),
                            company=company,
                            url=url,
                            location=item.get("location", "India"),
                            experience=item.get("experience", "") or "Not specified",
                            posted_at=str(posted_raw or "Unknown"),
                            description=item.get("description", "") or startup.get("high_concept", ""),
                            source=source,
                            posted_datetime=posted_dt,
                        )
                    )
            except json.JSONDecodeError:
                pass

        soup = BeautifulSoup(response.text, "lxml")
        for card in soup.select('[data-test="JobCard"], .job-card, article'):
            title_el = card.select_one("a[href*='/jobs/'], h2 a, h3 a")
            if not title_el:
                continue
            url = title_el.get("href", "")
            if url and not url.startswith("http"):
                url = urljoin("https://wellfound.com", url)
            company_el = card.select_one('[data-test="StartupName"], .company, .startup-name')
            loc_el = card.select_one('[data-test="Location"], .location')
            posted_raw = "Unknown"
            jobs.append(
                _normalize_job(
                    title=title_el.get_text(strip=True),
                    company=company_el.get_text(strip=True) if company_el else "",
                    url=url,
                    location=loc_el.get_text(strip=True) if loc_el else "India",
                    experience="Not specified",
                    posted_at=posted_raw,
                    description=card.get_text(" ", strip=True)[:2000],
                    source=source,
                    posted_datetime=None,
                )
            )

        jobs.extend(_extract_json_ld_jobs(response.text, source))
        logger.info("Wellfound HTML returned %d jobs", len(jobs))
    except Exception as exc:
        logger.error("Wellfound scrape failed: %s", exc)
        raise

    return _dedupe_by_url(jobs)


def scrape_linkedin(apify_api_key: str) -> list[dict[str, Any]]:
    """Scrape LinkedIn PM jobs in India via Apify actor."""
    source = "linkedin"
    if not apify_api_key:
        logger.warning("APIFY_API_KEY not set; skipping LinkedIn")
        return []

    # apimaestro~linkedin-jobs-scraper-api uses keywords + location_id (LinkedIn geoId).
    # India geoId = 102713980. Results are wrapped: {"status": "success", "results": [...]}
    actor_id = "apimaestro~linkedin-jobs-scraper-api"
    payload = {
        "keywords": "product manager",
        "location_id": "102713980",  # India
    }

    last_error: Exception | None = None
    run_url = f"https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"
    try:
        response = requests.post(
            run_url,
            params={"token": apify_api_key},
            json=payload,
            timeout=300,
        )
        response.raise_for_status()
        data = response.json()
        # Actor returns either a list or {"status":..., "results": [...]}
        if isinstance(data, list):
            raw_items = data
        elif isinstance(data, dict):
            raw_items = data.get("results") or data.get("jobs") or []
        else:
            raw_items = []
        jobs: list[dict[str, Any]] = []
        for item in raw_items:
            title = item.get("job_title") or item.get("title") or ""
            company = item.get("company") or item.get("companyName") or ""
            job_url = item.get("job_url") or item.get("link") or item.get("url") or ""
            location = item.get("location") or item.get("job_location") or "India"
            experience = item.get("experience_level") or item.get("experience") or "Not specified"
            posted_raw = item.get("posted_at") or item.get("listed_at") or item.get("datePosted") or ""
            posted_dt = _parse_relative_posted(str(posted_raw))
            description = item.get("description") or item.get("job_description") or ""
            if title and job_url:
                jobs.append(
                    _normalize_job(
                        title=title,
                        company=company,
                        url=job_url,
                        location=location,
                        experience=str(experience),
                        posted_at=str(posted_raw or "Unknown"),
                        description=description,
                        source=source,
                        posted_datetime=posted_dt,
                    )
                )
        logger.info("LinkedIn (Apify/%s) returned %d jobs", actor_id, len(jobs))
        return _dedupe_by_url(jobs)
    except Exception as exc:
        last_error = exc
        logger.warning("LinkedIn Apify actor %s failed: %s", actor_id, exc)

    if last_error:
        raise last_error
    return []


def _dedupe_by_url(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for job in jobs:
        url = job.get("url", "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(job)
    return unique


def scrape_all(apify_api_key: str = "") -> list[dict[str, Any]]:
    """Run all scrapers, continuing if individual sources fail."""
    all_jobs: list[dict[str, Any]] = []
    scrapers = [
        ("naukri", scrape_naukri),
        ("instahyre", scrape_instahyre),
        ("cutshort", scrape_cutshort),
        ("wellfound", scrape_wellfound),
        ("linkedin", lambda: scrape_linkedin(apify_api_key)),
    ]

    for name, fn in scrapers:
        try:
            jobs = fn()
            logger.info("Source %s: %d jobs scraped", name, len(jobs))
            all_jobs.extend(jobs)
        except Exception as exc:
            logger.error("Source %s failed: %s", name, exc)

    return _dedupe_by_url(all_jobs)
