# PM Job Alert Bot

A free, twice-daily email digest of Product Manager roles tailored to your background. Scrapes five job boards, applies hard filters, scores matches with Claude Haiku, and emails a ranked HTML digest at **9 AM** and **5 PM IST** via GitHub Actions.

## How it works

```
Scrape (5 sources) → Hard filters → Deduplicate (SQLite) → Claude scoring → Gmail digest
```

| Source | Search |
|--------|--------|
| Naukri.com | "product manager" in Bangalore, last 24h |
| Instahyre.com | "product manager" |
| Cutshort.io | "product manager" in Bangalore |
| Wellfound.com | "product manager" in India |
| LinkedIn | Via Apify actor, India, last 24h |

Only jobs scoring **≥ 6/10** are included in the email.

## Project structure

```
pm-job-alert-bot/
├── main.py              # Orchestrator
├── scraper.py           # All source scrapers
├── filter.py            # Hard filters
├── scorer.py            # Claude Haiku scoring
├── emailer.py           # HTML email + Gmail SMTP
├── requirements.txt
├── jobs_seen.db         # Auto-created; cached in GitHub Actions
└── .github/workflows/job_alert.yml
```

## Local setup

### 1. Clone and install

```bash
git clone <your-repo-url>
cd pm-job-alert-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Gmail App Password

1. Enable [2-Step Verification](https://myaccount.google.com/security) on your Google account.
2. Go to [App Passwords](https://myaccount.google.com/apppasswords).
3. Create an app password for **Mail** on **Other (Custom name)** — e.g. `pm-job-bot`.
4. Copy the 16-character password (no spaces).

### 3. Anthropic API key

1. Sign up at [console.anthropic.com](https://console.anthropic.com/).
2. Create an API key under **API Keys**.
3. Haiku is used by default (`claude-3-5-haiku-20241022`) for low cost.

### 4. Apify API key (LinkedIn)

1. Sign up at [apify.com](https://apify.com/) (free tier includes monthly credits).
2. Go to **Settings → Integrations → API tokens**.
3. Copy your personal API token.

The bot uses Apify's LinkedIn Jobs Scraper by [apimaestro](https://apify.com/apimaestro/linkedin-jobs-scraper-api). If the legacy `linkedin-jobs-scraper` actor is unavailable, it automatically falls back to `linkedin-jobs-scraper-api`.

### 5. Run locally

```bash
export GMAIL_APP_PASSWORD="your-16-char-app-password"
export ANTHROPIC_API_KEY="sk-ant-..."
export APIFY_API_KEY="apify_api_..."

python main.py
```

Optional env vars:

| Variable | Default | Description |
|----------|---------|-------------|
| `EMAIL_RECIPIENT` | `sumaantamunde@gmail.com` | Digest recipient |
| `EMAIL_SENDER` | `sumaantamunde@gmail.com` | Gmail sender (must match app password account) |
| `JOBS_DB_PATH` | `jobs_seen.db` | SQLite dedup database |
| `MIN_JOB_SCORE` | `6` | Minimum score to include in digest |
| `ANTHROPIC_MODEL` | `claude-3-5-haiku-20241022` | Claude model for scoring |

## GitHub Actions deployment (free scheduling)

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "Add PM job alert bot"
git remote add origin git@github.com:<you>/pm-job-alert-bot.git
git push -u origin main
```

### 2. Add repository secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|--------|-------|
| `GMAIL_APP_PASSWORD` | Gmail app password |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `APIFY_API_KEY` | Apify API token |

### 3. Enable Actions

- Go to **Actions** tab and enable workflows.
- The workflow runs on cron:
  - `30 3 * * *` → 9:00 AM IST
  - `30 11 * * *` → 5:00 PM IST
- You can also trigger manually via **Run workflow**.

### 4. Deduplication cache

`jobs_seen.db` is persisted across runs using `actions/cache`, so jobs are never emailed twice.

## Email format

**Subject:** `🔔 PM Jobs Digest – [9AM/5PM], [Date] ([N] new roles)`

Each job shows:
- Role title + company (linked)
- Score /10 with one-line reason
- Experience | Location | Posted time
- 2-sentence company blurb (from Claude)

Jobs are grouped by score (descending). An email is sent even when zero new roles match — you'll get a "no new roles" digest.

## Filtering criteria (hard filters)

- Posted in last 24 hours
- PM titles only (Product Manager, APM, Senior PM, Group PM, Head of Product)
- Excludes Product Marketing, Program Manager, Technical PM requiring active coding
- Max experience required ≤ 6 years
- Location: Bangalore, Remote, or Pan-India

## Scoring rubric (Claude)

| Signal | Weight |
|--------|--------|
| Early-stage startup | +3 |
| Series A/B | +2 |
| Late stage | +1 |
| Consumer / social / fintech / B2B SaaS | +2 |
| Values eng-to-PM or founding experience | +2 |
| Requires 5+ yrs PM title or FAANG signals | −3 |

## Error handling

If one scraper fails, the bot logs the error and continues with remaining sources. The digest is still sent with whatever jobs were found.

## Cost estimate (monthly)

| Service | Est. cost |
|---------|-----------|
| GitHub Actions | Free (within limits) |
| Gmail SMTP | Free |
| Claude Haiku | ~$0.50–2 depending on volume |
| Apify free tier | Free for ~2 runs/day |

## Troubleshooting

**No emails received**
- Check Gmail App Password is correct and 2FA is enabled.
- Verify `EMAIL_SENDER` matches the Gmail account that owns the app password.
- Check Actions logs under the **PM Job Alert Digest** workflow.

**LinkedIn jobs missing**
- Confirm `APIFY_API_KEY` secret is set.
- Check Apify dashboard for remaining free credits.

**Same job resent**
- Ensure `actions/cache` step is not failing; cache key must persist `jobs_seen.db`.

**Naukri returns empty**
- Naukri uses bot protection; the scraper tries API first, then HTML. GitHub Actions runners usually work better than local networks with strict firewalls.

## License

MIT — use and modify freely.
