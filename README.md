# Immigration Scrape & Email Pipeline

Python pipeline that discovers immigration service providers on the web, extracts contact emails via browser scraping, and sends partnership outreach using a Gmail SMTP profile.

Built to follow patterns from `CVL-ScraperLinkedIn_SendMails` (SQLite deduplication, Gmail sending, logging) with browser-based discovery instead of LinkedIn.

---

## What it does

1. **Generate search queries** — NVIDIA NIM (with API-key rotation) creates varied Google search phrases for immigration consultants, visa agencies, and related firms.
2. **Scrape websites** — Playwright opens Google results in Chrome (falls back to Chromium, then Firefox). For each company site:
   - Waits up to 3 minutes for slow pages
   - Scans the full page for `@` email addresses
   - Clicks Contact / Contact Us if no email on the landing page
   - Skips the site if no valid email is found
3. **Store in SQLite** — Companies, emails, search queries, and sent-mail records are saved with uniqueness constraints to prevent duplicates.
4. **Send emails** — Uses `partnership.html`, a positive one-line company praise from NVIDIA, and Gmail app-password SMTP from `EmailJson`.

---

## Project structure

```
investment/
├── immigration_pipeline.py   # Main CLI entry point
├── immigration_scraper.py    # Browser scraping (Google + company sites)
├── immigration_sender.py     # Gmail SMTP sender
├── immigration_db.py         # SQLite schema and helpers
├── nvidia_llm.py             # NVIDIA key rotation + LLM calls
├── partnership.html          # Email HTML template
├── sender_config.json        # Sender details + emails_per_run limit
├── requirements.txt
├── data/db/immigration.db    # SQLite database (created on first run)
└── logs/                     # Timestamped run logs
```

---

## Prerequisites

- Python 3.10+
- Windows (sleep-prevention helper is Windows-specific; other OS paths still work)
- Google Chrome or Firefox installed
- Gmail account with an [App Password](https://myaccount.google.com/apppasswords)
- NVIDIA API keys in `C:\Users\sandeep\Downloads\Claudes\nvidia_keys\` (`key1.json`, `key2.json`, …)

---

## Setup

```powershell
cd C:\Users\sandeep\Downloads\Claudes\investment
pip install -r requirements.txt
playwright install chromium firefox
```

---

## Configuration

### `sender_config.json`

Controls sender identity, email subject, and **how many emails to send per execution**.

```json
{
  "sender_name": "Sandeep Jain",
  "company_name": "PlacementsHub",
  "phone": "+91-XXXXXXXXXX",
  "email": "jain1001sandeep@gmail.com",
  "website": "https://your-site.example",
  "signature_links": [
    { "label": "PlacementsHub", "url": "https://your-site.example" },
    { "label": "My CV", "url": "https://drive.google.com/file/d/..." },
    { "label": "Detailed Profile", "url": "https://jainsandeep729.wixsite.com/website" },
    { "label": "LinkedIn Profile", "url": "https://www.linkedin.com/in/jain35/" }
  ],
  "email_subject": "Exploring a potential partnership opportunity",
  "emails_per_run": 2
}
```

| Field | Purpose |
|-------|---------|
| `sender_name` | Replaces `{{SenderName}}` in the signature |
| `company_name` | Replaces `{{CompanyName}}` in the signature |
| `phone` | Replaces `{{Phone}}` in the signature |
| `email` | Replaces `{{Email}}` in the signature |
| `website` | Fallback if `signature_links` is empty |
| `signature_links` | Array of `{ "label", "url" }` — all clickable footer links (website, CV, profile, etc.) |
| `email_subject` | SMTP subject line |
| `emails_per_run` | **Max emails sent in one `send` or `run` execution** (default: 2) |

Override at runtime with `--limit` (`send`) or `--send-limit` (`run`).

### SMTP credentials

Default path:

```
C:\Users\sandeep\Downloads\Claudes\EmailJson\email_config1001.json
```

Format:

```json
{
  "profiles": {
    "your.email@gmail.com": "xxxx xxxx xxxx xxxx"
  }
}
```

Override with environment variable:

```powershell
$env:EMAIL_CONFIG_FILE = "C:\path\to\email_config.json"
```

### NVIDIA keys

Keys are loaded from `C:\Users\sandeep\Downloads\Claudes\nvidia_keys\` and rotated automatically on each LLM call.

Override directory:

```powershell
$env:NVIDIA_KEYS_DIR = "C:\path\to\nvidia_keys"
```

Each key file:

```json
{ "api_key": "nvapi-..." }
```

### Email template — `partnership.html`

| Placeholder | Filled with |
|-------------|-------------|
| `{{RecipientCompany}}` | Scraped company name |
| `{{CompanyPraise}}` | One positive sentence from NVIDIA about the company |
| `{{SenderName}}` | `sender_name` from `sender_config.json` |
| `{{CompanyName}}` | `company_name` from `sender_config.json` |
| `{{Phone}}` | `phone` from `sender_config.json` |
| `{{Email}}` | `email` from `sender_config.json` (mailto link) |
| `{{SignatureLinks}}` | All entries from `signature_links` in `sender_config.json` |

Email signature block (links driven by `signature_links` in JSON):

```
Best Regards,
Sandeep Jain
PlacementsHub
+91-9860090620
sandeepjain200019@gmail.com
PlacementsHub          ← signature_links
My CV                  ← signature_links
Detailed Profile       ← signature_links
LinkedIn Profile       ← signature_links
```

All signature links (website, CV, detailed profile, LinkedIn, etc.) are defined in the `signature_links` array in `sender_config.json`. Add, remove, or reorder entries there — no code changes needed.

---

## Commands

```powershell
# Show database counts
python immigration_pipeline.py status

# Generate Google search queries via NVIDIA
python immigration_pipeline.py seed-keywords --count 15 --region India

# Scrape immigration providers (browser opens visibly)
python immigration_pipeline.py scrape --max-companies 20 --browser auto

# Preview messages without sending
python immigration_pipeline.py send --dry-run

# Send emails (capped by emails_per_run in sender_config.json)
python immigration_pipeline.py send

# Scrape then send in one run
python immigration_pipeline.py run --max-companies 15
```

### Useful flags

| Command | Flag | Description |
|---------|------|-------------|
| `scrape` | `--max-companies N` | Stop after N new company sites (default: 20) |
| `scrape` | `--max-queries N` | Process N search queries per run (default: 5) |
| `scrape` | `--browser auto\|chrome\|chromium\|firefox` | Browser choice (default: auto) |
| `scrape` | `--no-seed` | Do not auto-generate queries if queue is empty |
| `send` | `--dry-run` | Build messages only; no SMTP |
| `send` | `--limit N` | Override `emails_per_run` for this run |
| `send` | `--no-nvidia-praise` | Use a static praise line instead of NVIDIA |
| `run` | `--send-limit N` | Override `emails_per_run` for the send step |

---

## How scraping works

```
NVIDIA generates search queries
        ↓
Google search in browser (Chrome preferred)
        ↓
For each result URL (deduped by domain):
        ↓
Visit landing page (up to 180s timeout)
        ↓
Extract emails from full HTML + visible text
        ↓
If none found → click Contact / Contact Us
        ↓
Re-scan page for @ addresses
        ↓
Save to SQLite or mark as no_email
```

**Deduplication**

- `companies.domain` — unique; same website is never scraped twice
- `company_emails.email` — unique across all companies
- `email_sent.email` — unique; already-sent addresses are skipped

**Email selection for sending**

When a company has multiple addresses (e.g. branch inboxes), the sender picks one per company, preferring: `info@`, `contact@`, `enquiries@`, `hello@`, `admin@`, etc.

---

## SQLite tables

| Table | Purpose |
|-------|---------|
| `search_queries` | Google search phrases and completion status |
| `companies` | Company name, website, domain, email scrape status |
| `company_emails` | Emails found per company |
| `email_sent` | Sent / failed outreach records |

Database path: `data/db/immigration.db`

---

## Sending behaviour

- Gmail SMTP over SSL on port 465
- 5 second delay between sends
- 20 second cooldown per recipient domain
- One email per company per run (best address only)
- `emails_per_run` from `sender_config.json` caps each execution (currently **2**)

---

## Logs

Each run writes to:

```
logs/immigration_YYYY-MM-DD_HH-MM-SS.log
```

---

## Troubleshooting

| Issue | What to try |
|-------|-------------|
| Browser does not open | Run `playwright install chromium firefox` |
| Chrome fails | Use `--browser firefox` |
| NVIDIA timeout | Keys rotate automatically; run again or check key quota |
| SMTP auth error | Regenerate Gmail App Password in `email_config1001.json` |
| No emails found | Normal for some sites; pipeline moves on |
| Google consent / CAPTCHA | Complete manually in the visible browser window |
| Want more emails per run | Increase `emails_per_run` in `sender_config.json` |

---

## Related projects

| Path | Used for |
|------|----------|
| `..\CVL-ScraperLinkedIn_SendMails\` | SMTP patterns, SQLite email tracking, Playwright setup |
| `..\EmailJson\` | Gmail app-password profiles |
| `..\nvidia_keys\` | Rotating NVIDIA NIM API keys |

---

## Example workflow

```powershell
# 1. Configure sender details and emails_per_run
notepad sender_config.json

# 2. Seed search keywords
python immigration_pipeline.py seed-keywords --count 10 --region India

# 3. Scrape a small batch first
python immigration_pipeline.py scrape --max-companies 10

# 4. Check results
python immigration_pipeline.py status

# 5. Dry-run then send
python immigration_pipeline.py send --dry-run
python immigration_pipeline.py send
```

Each `send` run sends at most `emails_per_run` new emails (2 by default). Run again later to continue through the queue.
