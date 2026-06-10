"""HTML email builder and Gmail SMTP sender."""

from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Any

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
RECIPIENT = os.getenv("EMAIL_RECIPIENT", "sumaantamunde@gmail.com")
SENDER = os.getenv("EMAIL_SENDER", "sumaantamunde@gmail.com")


def _next_run_label(now: datetime | None = None) -> str:
    now = now or datetime.now(IST)
    morning = now.replace(hour=9, minute=0, second=0, microsecond=0)
    evening = now.replace(hour=17, minute=0, second=0, microsecond=0)

    if now < morning:
        return "9:00 AM IST today"
    if now < evening:
        return "5:00 PM IST today"
    tomorrow = now + timedelta(days=1)
    return f"9:00 AM IST on {tomorrow.strftime('%d %b %Y')}"


def _digest_slot(now: datetime | None = None) -> str:
    now = now or datetime.now(IST)
    hour = now.hour
    if 8 <= hour < 14:
        return "9AM"
    return "5PM"


def build_subject(
    jobs: list[dict[str, Any]],
    twitter_signals: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(IST)
    slot = _digest_slot(now)
    date_str = now.strftime("%d %b %Y")
    count = len(jobs)
    signal_part = f" · {len(twitter_signals)} tweet signal{'s' if len(twitter_signals) != 1 else ''}" if twitter_signals else ""
    return f"🔔 PM Jobs Digest – {slot}, {date_str} ({count} new roles{signal_part})"


def _build_pm_contacts_html(job: dict[str, Any]) -> str:
    """Return the PM outreach block HTML for a single job."""
    score = job.get("score", 0)
    company = escape(job.get("company", "this company"))
    contacts = job.get("pm_contacts")

    if score < 8:
        return """
        <div style="margin-top:10px;padding:10px 12px;background:#f9fafb;border-left:3px solid #d1d5db;border-radius:4px;">
          <span style="font-size:12px;color:#9ca3af;">
            👤 Outreach targets: not fetched (score below 8 — save Apify credits)
          </span>
        </div>"""

    if not contacts:
        return """
        <div style="margin-top:10px;padding:10px 12px;background:#f9fafb;border-left:3px solid #d1d5db;border-radius:4px;">
          <span style="font-size:12px;color:#9ca3af;">
            👤 No active PM contacts found at this company on LinkedIn.
          </span>
        </div>"""

    rows = ""
    for c in contacts:
        name = escape(c.get("full_name") or "Unknown")
        reason = escape(c.get("outreach_reason") or "")
        linkedin_url = escape(c.get("linkedin_url") or "#")
        rows += f"""
        <li style="margin-bottom:8px;font-size:13px;color:#374151;">
          <strong>{name}</strong> — {reason}<br>
          <a href="{linkedin_url}" style="color:#1a56db;font-size:12px;">{linkedin_url}</a>
        </li>"""

    return f"""
    <div style="margin-top:12px;padding:12px 14px;background:#eff6ff;border-left:3px solid #3b82f6;border-radius:4px;">
      <div style="font-size:13px;font-weight:600;color:#1e40af;margin-bottom:8px;">
        👤 Warm outreach targets at {company}:
      </div>
      <ul style="margin:0;padding-left:16px;">{rows}</ul>
    </div>"""


def _build_twitter_section_html(signals: list[dict[str, Any]]) -> str:
    """Return the full Twitter Hiring Signals section HTML."""
    if not signals:
        return ""

    items = ""
    for s in signals:
        handle = escape(s.get("author_handle") or "")
        nb_views = s.get("follower_count", 0)
        text = escape((s.get("tweet_text") or "")[:150])
        score = s.get("tweet_score", 0)
        created_at = escape(s.get("created_at") or "")
        url = escape(s.get("url") or "#")
        if len(s.get("tweet_text") or "") > 150:
            text += "…"
        views_fmt = f"{nb_views:,} views" if nb_views else ""
        meta = f" ({views_fmt})" if views_fmt else ""
        items += f"""
        <li style="margin-bottom:14px;padding-bottom:14px;border-bottom:1px solid #e5e7eb;font-size:13px;color:#374151;">
          <span style="font-weight:600;color:#111;">@{handle}</span>
          <span style="color:#9ca3af;">{meta}</span>
          — {text}<br>
          <span style="color:#6b7280;font-size:12px;">
            Score: {score}/5 · {created_at} ·
            <a href="{url}" style="color:#1a56db;">view tweet</a>
          </span>
        </li>"""

    return f"""
    <tr>
      <td style="padding-top:24px;">
        <div style="border-top:2px solid #f59e0b;padding-top:16px;margin-top:8px;">
          <h2 style="margin:0 0 14px;font-size:16px;color:#92400e;">📣 Twitter Hiring Signals</h2>
          <ul style="margin:0;padding-left:0;list-style:none;">{items}</ul>
        </div>
      </td>
    </tr>"""


def build_html_email(
    jobs: list[dict[str, Any]],
    twitter_signals: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(IST)
    slot = _digest_slot(now)
    date_str = now.strftime("%d %b %Y")
    next_run = _next_run_label(now)
    twitter_signals = twitter_signals or []

    if not jobs:
        body_rows = """
        <tr><td style="padding:24px;color:#555;font-size:15px;">
          No new PM roles matched your criteria in this run. The bot will check again at the next scheduled time.
        </td></tr>
        """
    else:
        body_rows = ""
        for job in jobs:
            title = escape(job.get("title", "Role"))
            company = escape(job.get("company", "Company"))
            url = escape(job.get("url", "#"))
            score = job.get("score", 0)
            reason = escape(job.get("reason", ""))
            experience = escape(job.get("experience", "Not specified"))
            location = escape(job.get("location", "Not specified"))
            posted = escape(job.get("posted_at", "Unknown"))
            blurb = escape(job.get("company_blurb", "Company details unavailable."))
            pm_contacts_html = _build_pm_contacts_html(job)

            body_rows += f"""
            <tr>
              <td style="padding:18px 0;border-bottom:1px solid #ececec;">
                <div style="font-size:18px;font-weight:600;margin-bottom:6px;">
                  <a href="{url}" style="color:#1a56db;text-decoration:none;">{title}</a>
                  <span style="color:#444;"> @ {company}</span>
                </div>
                <div style="font-size:14px;color:#0f766e;margin-bottom:8px;">
                  Score: {score}/10 — {reason}
                </div>
                <div style="font-size:13px;color:#666;margin-bottom:8px;">
                  {experience} | {location} | {posted}
                </div>
                <div style="font-size:14px;color:#333;line-height:1.5;">{blurb}</div>
                {pm_contacts_html}
              </td>
            </tr>
            """

    twitter_section = _build_twitter_section_html(twitter_signals)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f6f8fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f6f8fb;padding:24px 0;">
    <tr>
      <td align="center">
        <table width="640" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;padding:28px;box-shadow:0 2px 8px rgba(0,0,0,0.06);">
          <tr>
            <td style="padding-bottom:16px;border-bottom:2px solid #1a56db;">
              <h1 style="margin:0;font-size:22px;color:#111;">PM Jobs Digest</h1>
              <p style="margin:6px 0 0;color:#666;font-size:14px;">{_digest_slot(now)} run · {date_str} · {len(jobs)} new role(s)</p>
            </td>
          </tr>
          {body_rows}
          {twitter_section}
          <tr>
            <td style="padding-top:20px;color:#888;font-size:12px;text-align:center;">
              Powered by your job alert bot | Next run: {next_run}
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def send_email(
    jobs: list[dict[str, Any]],
    *,
    twitter_signals: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    subject: str | None = None,
) -> None:
    app_password = os.getenv("GMAIL_APP_PASSWORD")
    if not app_password:
        raise ValueError("GMAIL_APP_PASSWORD environment variable is not set")

    twitter_signals = twitter_signals or []
    now = now or datetime.now(IST)
    subject = subject or build_subject(jobs, twitter_signals, now)
    html_body = build_html_email(jobs, twitter_signals, now)
    plain_body = (
        f"PM Jobs Digest – {_digest_slot(now)}, {now.strftime('%d %b %Y')} "
        f"({len(jobs)} new roles)\n\n"
        "Open this email in HTML view for full formatting."
    )

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = SENDER
    message["To"] = RECIPIENT
    message.attach(MIMEText(plain_body, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(SENDER, app_password)
            server.sendmail(SENDER, [RECIPIENT], message.as_string())
        logger.info("Email sent to %s with %d jobs", RECIPIENT, len(jobs))
    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
        raise
