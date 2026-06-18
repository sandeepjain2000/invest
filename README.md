# Partnership Scrape & Email Pipeline

Python pipeline that discovers strategic partner companies across **15 industry verticals**, extracts contact emails via browser scraping, and sends partnership outreach via **Brevo API** (legacy Gmail SMTP optional).

Built to follow patterns from `CVL-ScraperLinkedIn_SendMails` (SQLite deduplication, sending, logging) with browser-based discovery instead of LinkedIn.

**Two databases:**

| Database | Tool | Purpose |
|----------|------|---------|
| `data/db/immigration.db` | `immigration_pipeline.py` | Scrape + send partnership emails |
| `data/db/ca_bulk.db` | `ca_bulk_import.py` | Bulk resumable CA Connect email harvest (1000+ contacts) |

---

## Industry verticals

Fifteen industry verticals are defined in `industries.json`:

| Rank | ID | Sector | Email template |
|------|-----|--------|----------------|
| 1 | `recruitment_staffing` | Recruitment & Staffing Firms | `partnership_recruitment.html` |
| 2 | `edtech` | EdTech Companies | `partnership_edtech.html` |
| 3 | `overseas_education_immigration` | Overseas Education / Immigration | `partnership_immigration.html` |
| 4 | `education_finance` | Student Loan / Education Finance | `partnership_education_finance.html` |
| 5 | `hrtech` | HRTech Companies | `partnership_hrtech.html` |
| 6 | `corporate_csr` | Corporate CSR Programs | `partnership_csr.html` |
| 7 | `lawyers` | Law Firms & Lawyers | `partnership_lawyers.html` |
| 8 | `training_finishing_schools` | Training & Finishing Schools | `partnership_training.html` |
| 9 | `bfsi_nbfc_banks` | NBFC & Banks (Campus Hiring) | `partnership_bfsi.html` |
| 10 | `insurance` | Insurance (Campus & Graduate Hiring) | `partnership_insurance.html` |
| 11 | `coaching_test_prep` | Coaching & Test Prep | `partnership_coaching.html` |
| 12 | `ca_cs_firms` | Chartered Accountants & Company Secretaries | `funding_intro.html` (investor intros) |
| 13 | `company_secretary_firms` | Company Secretary Firms | `funding_intro_cs.html` |
| 14 | `tax_consultants` | Tax Consultants | `funding_intro_tax.html` |
| 15 | `wealth_managers` | Wealth Managers & Investment Advisors | `funding_intro_wealth.html` |

Each industry has its own `template_file`, `email_subject`, and optional `signature_links` in `industries.json`.

- **Partnership verticals (11)** — direct benefit-led copy (`partnership_*.html`): opens with *Direct benefit for {{RecipientCompany}}*, strategic bullet list, one-line pitch.
- **Investor-intro verticals (4)** — `funding_intro*.html` for CA, CS, tax, and wealth managers (seed investor introductions + future mandate upside).

Each industry has static `seed_queries` and a `praise_hint` for NVIDIA-generated outreach lines. Set `"active": false` on any industry to skip it.

**Scrape source:** Almost all industries use **Google search → company website**. Only `ca_cs_firms` uses **CA Connect** (`scrape_source: "ca_connect"`). Reserved industries (`wealth_managers`, `tax_consultants`, etc.) are auto-seeded and preferred during scrape when their queue is below quota.

**Rank** is for reference only. When scraping all industries, the pipeline **shuffles industry order randomly** on each query pick so effort spreads across sectors.

---

## What it does

1. **Generate search queries** — NVIDIA NIM (with API-key rotation) creates Google search phrases per industry, or uses static seeds from `industries.json`.
2. **Scrape websites** — Playwright opens Google results in Chrome (falls back to Chromium, then Firefox). For each company site:
   - Waits up to 3 minutes for slow pages
   - Scans the full page for `@` email addresses
   - Clicks Contact / Contact Us if no email on the landing page
   - Skips the site if no valid email is found
3. **Store in SQLite** — Companies, emails, search queries, and sent-mail records are saved with uniqueness constraints to prevent duplicates.
4. **Send emails** — Renders the per-industry HTML template from `industries.json`, sends via **Brevo API** (or legacy Gmail SMTP).

---

## Project structure

```
investment/
├── immigration_pipeline.py     # Main CLI — scrape + send
├── immigration_scraper.py      # Google + company site scraping
├── immigration_sender.py       # Brevo / Gmail sender
├── immigration_db.py           # immigration.db schema
├── ca_bulk_import.py           # Bulk resumable CA email harvester
├── ca_bulk_db.py               # ca_bulk.db schema
├── ca_connect_pipeline.py      # CA Connect hook for main pipeline
├── ca_connect_scraper.py       # CA Connect browser scraper
├── pipeline_progress.py        # Terminal PROGRESS | lines during runs
├── pipeline_notify.py          # Beeps / voice on completion
├── nvidia_llm.py               # NVIDIA key rotation + LLM calls
├── industries.json             # 15 verticals + templates + seed queries
├── industries.py
├── partnership.html            # Fallback template only
├── partnership_*.html          # Per-industry partnership templates (11)
├── funding_intro*.html         # CA / CS / tax / wealth templates (4)
├── brevo_mail.py               # Brevo API client
├── mail_config.json            # Brevo keys (gitignored)
├── mail_config.example.json
├── requirements.txt
├── send_partnership_emails.bat # One-click scrape + send
├── run_ca_bulk_import.bat      # One-click bulk CA harvest (resume)
├── ca_bulk_status.bat          # Bulk DB status only
├── ca_bulk_import.bat          # Bulk CLI pass-through
├── ca_bulk_config.example.json # Bulk harvest config (copy → ca_bulk_config.json)
├── ca_connect_credentials.example.json
├── sender_config.json
├── data/db/immigration.db
├── data/db/ca_bulk.db          # Separate bulk CA database
├── data/ca_connect_results.json
└── logs/                       # immigration_*.log and ca_bulk_*.log
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

Controls sender identity, email subject, send limits, and **local HTML template**. Brevo credentials live in **`mail_config.json`** in this project (separate Brevo account from Scrape_aishe).

```json
{
  "sender_name": "Sandeep Jain",
  "company_name": "PlacementsHub",
  "phone": "+91-XXXXXXXXXX",
  "email": "sandeep.jain@appsflow.cloud",
  "website": "https://your-site.example",
  "signature_links": [
    { "label": "Detailed Profile", "url": "https://your-profile.example" }
  ],
  "email_subject": "Exploring a potential partnership opportunity",
  "emails_per_run": 32,
  "max_companies_per_run": 100,
  "max_queries_per_run": 40,
  "scrape_headless": false,
  "send_method": "brevo_api",
  "template_file": "partnership.html",
  "mail_config_file": "mail_config.json"
}
```

| Field | Purpose |
|-------|---------|
| `sender_name` | Replaces `{{SenderName}}` in the signature |
| `company_name` | Replaces `{{CompanyName}}` in the signature |
| `phone` | Replaces `{{Phone}}` in the signature |
| `email` | Replaces `{{Email}}` in the signature |
| `website` | Fallback if `signature_links` is empty |
| `signature_links` | Default footer links (overridden per industry in `industries.json`) |
| `email_subject` | Default subject base (overridden per industry in `industries.json`) |
| `emails_per_run` | Max emails sent per `send` / `run` (also scrape email target) |
| `max_companies_per_run` | Max company sites to visit per scrape run |
| `max_queries_per_run` | Max Google search queries per scrape run |
| `scrape_headless` | `true` = hide browser during Google scrape; default `false` |
| `ensure_industry_per_run` | Industry for CA Connect ensure (default: `ca_cs_firms`) |
| `min_ensure_ca_connect_per_run` | Min CA Connect contacts reserved in send queue (default: 8) |
| `min_ensure_industry_scrape_per_run` | Min companies with email from ensure industry per scrape |
| `reserved_send_by_industry` | Min send slots per run for e.g. CS, tax, wealth managers |
| `ca_connect_enabled` | Run CA Connect scrape at start of pipeline scrape |
| `ca_connect_profiles_per_run` | Profile pages to enrich per pipeline run (default: 10) |
| `ca_connect_credentials_file` | Login JSON for CA Connect profile pages |
| `send_method` | `brevo_api` (default) or `gmail_smtp` |
| `template_file` | Fallback HTML if industry has no `template_file` in `industries.json` |
| `mail_config_file` | Path to Brevo config |

Override at runtime with `--limit` (`send`) or `--send-limit` (`run`).

### `mail_config.json` — Brevo API + SMTP (this project)

**Separate Brevo account** for partnership outreach. Copy from the example if needed:

```powershell
copy mail_config.example.json mail_config.json
```

Edit `mail_config.json` with your Brevo keys:

```json
{
  "brevo": {
    "api_key": "YOUR_API_V3_KEY",
    "sender": {
      "name": "Sandeep Jain",
      "email": "sandeep.jain@appsflow.cloud"
    }
  },
  "smtp": {
    "host": "smtp-relay.brevo.com",
    "port": 587,
    "login": "your-brevo-smtp-login",
    "password": "YOUR_SMTP_KEY",
    "use_tls": true
  }
}
```

| Section | Field | Purpose |
|---------|-------|---------|
| `brevo` | `api_key` | Brevo API v3 key — used by `send_method: brevo_api` (default) |
| `brevo.sender` | `name`, `email` | Verified sender in Brevo (Zoho inbox: `sandeep.jain@appsflow.cloud`) |
| `smtp` | `host`, `port`, `login`, `password`, `use_tls` | Brevo SMTP relay credentials (for reference / future SMTP send) |

`mail_config.json` is **gitignored** so keys are not committed. `mail_config.example.json` stays in the repo as a template.

- Override path: `$env:MAIL_CONFIG_FILE = "C:\path\to\mail_config.json"`
- Override API key only: `$env:BREVO_API_KEY = "xkeysib-..."`

### Brevo sending

- **Default transport:** Transactional API (`send_method: brevo_api`)
- **Template:** local `partnership.html` (filled in Python, sent as `html_content`)
- **Sender inbox:** `sandeep.jain@appsflow.cloud` (Zoho Mail; replies forwarded to Gmail)
- Legacy Gmail: `"send_method": "gmail_smtp"` + `email_config1001.json`

### SMTP credentials (legacy Gmail only)

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

### Email templates

Each industry uses its own HTML file (see table above). Per-industry settings in `industries.json`:

```json
"template_file": "partnership_immigration.html",
"email_subject": "Privileged student lifecycle access for overseas education consultants",
"subject_append_domain": true,
"use_nvidia_praise": true,
"signature_links": [ { "label": "...", "url": "..." } ]
```

| Placeholder | Filled with |
|-------------|-------------|
| `{{RecipientCompany}}` | Scraped company or CA name |
| `{{CompanyPraise}}` | One positive sentence from NVIDIA (when enabled) |
| `{{SenderName}}` | `sender_name` from `sender_config.json` |
| `{{CompanyName}}` | `company_name` from `sender_config.json` |
| `{{Phone}}` | `phone` |
| `{{Email}}` | `email` (mailto link) |
| `{{SignatureLinks}}` | Industry `signature_links`, or fallback from `sender_config.json` |

Subject line: base from industry config; appends ` with {domain}` when `subject_append_domain` is true.

---

## One-click run (Windows)

### Partnership pipeline — `send_partnership_emails.bat`

Double-click or run from the project folder. Each execution:

1. **Scrape** — CA Connect (headless, 10 profiles/run) + Google (visible browser by default)
2. **Send** — up to `emails_per_run` emails via Brevo with per-industry templates

Optional overrides at top of the bat file:

```bat
set "SEND_LIMIT="
set "MAX_COMPANIES="
set "MAX_QUERIES="
set "HEADLESS=1"    rem hide Google browser
set "REGION=India"
```

**Terminal progress:** lines prefixed with `PROGRESS |` show scrape/send counters live. **RUN SUMMARY** at the end shows per-industry counts.

Current defaults in `sender_config.json`:

| Setting | Default | Meaning |
|---------|---------|---------|
| `emails_per_run` | 32 | Send target + scrape stops after this many companies with email |
| `max_companies_per_run` | 100 | Max sites to open per scrape |
| `max_queries_per_run` | 40 | Max Google searches per scrape |
| `ca_connect_profiles_per_run` | 10 | CA profile pages enriched per run (main pipeline) |

Reply handling: outreach sends from **`sandeep.jain@appsflow.cloud`** (Zoho → forwarded to Gmail). `check_replies_before_send` stays off by default.

---

## Commands

```powershell
# List all industry verticals
python immigration_pipeline.py list-industries

# Show database counts (including per-industry breakdown)
python immigration_pipeline.py status

# Seed queries for ALL active industries (static seeds, fast)
python immigration_pipeline.py seed-keywords --all --no-nvidia

# Seed one industry via NVIDIA
python immigration_pipeline.py seed-keywords --industry recruitment_staffing

# Scrape across all industries (browser opens visibly)
python immigration_pipeline.py scrape --max-companies 20 --browser auto

# Scrape one industry only
python immigration_pipeline.py scrape --industry edtech --max-companies 10

# Preview next real outreach (no email sent)
python immigration_pipeline.py send --dry-run --limit 1

# Preview test email to your inbox (last scraped company; no send, no DB update)
python immigration_pipeline.py send --test-to sandeepjain200019@gmail.com --dry-run

# Send real test email to your inbox (last scraped company; no DB update)
python immigration_pipeline.py send --test-to sandeepjain200019@gmail.com

# Send emails (reply check off by default)
python immigration_pipeline.py send

# Optional legacy only: Gmail IMAP reply scan (not needed — Zoho forwards to Gmail)
# python immigration_pipeline.py check-replies

# Scrape then send in one run
python immigration_pipeline.py run --max-companies 15
```

### Send more emails per run

Default is **32** emails per execution (`emails_per_run` in `sender_config.json`).

```powershell
python immigration_pipeline.py run --send-limit 50
python immigration_pipeline.py send --limit 50
python immigration_pipeline.py run --headless          # Google scrape without visible window
python immigration_pipeline.py run --no-headless       # force visible browser
python immigration_pipeline.py seed-keywords --industry wealth_managers
```

Permanent default — edit `sender_config.json`:

```json
"emails_per_run": 32,
"max_companies_per_run": 100,
"max_queries_per_run": 40
```

### Replies (Zoho Mail → Gmail — no pipeline step)

**Sender / inbox:** `sandeep.jain@appsflow.cloud` — this is your **Zoho Mail** address (also the verified Brevo sender and email signature).

**Flow:**
1. Brevo delivers outreach from that address
2. Replies arrive in the Zoho inbox for `sandeep.jain@appsflow.cloud`
3. Zoho forwards everything to `sandeepjain200019@gmail.com`

You do not need `check-replies` or `forward_to` for normal operation. `check_replies_before_send` stays `false` in `sender_config.json`.

### Useful flags

| Command | Flag | Description |
|---------|------|-------------|
| `seed-keywords` | `--all` | Seed every active industry in `industries.json` |
| `seed-keywords` | `--industry ID` | Seed one vertical (e.g. `edtech`, `hrtech`) |
| `seed-keywords` | `--no-nvidia` | Use static `seed_queries` from JSON only |
| `scrape` / `run` | `--industry ID` | Limit scraping to one industry |
| `scrape` | `--max-companies N` | Stop after N new company sites (default: 20) |
| `scrape` | `--max-queries N` | Process N search queries per run (default: 5) |
| `scrape` | `--browser auto\|chrome\|chromium\|firefox` | Browser choice (default: auto) |
| `scrape` / `run` | `--headless` | Hide browser window during Google scrape |
| `scrape` / `run` | `--no-headless` | Force visible browser (overrides `scrape_headless` in JSON) |
| `scrape` | `--no-seed` | Do not auto-generate queries if queue is empty |
| `scrape` | `--no-nvidia-seed` | Auto-seed from `industries.json` only |
| `check-replies` | `--no-nvidia` | Skip NVIDIA for borderline replies |
| `send` | `--check-replies` | Scan Gmail and forward replies before send (off by default) |
| `send` | `--skip-replies` | Explicitly skip reply check (default behaviour) |
| `send` | `--test-to EMAIL` | One test send to your address (last scraped company; no DB update) |
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

**Google block detection:** if Google shows consent, CAPTCHA, or `/sorry/`, the terminal prints:

```
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
PROGRESS | GOOGLE BLOCK / CHALLENGE DETECTED
PROGRESS | Fix: run without --headless, complete consent/CAPTCHA in the browser
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
```

Also watch for repeated `Found 0 unique result(s).` on every query.

**CA Connect in main pipeline:** enriches up to `ca_connect_profiles_per_run` profiles per run. **`imported 0 new email(s)`** means those emails are already in `immigration.db` from a prior run — not a scrape failure.

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

- **Default:** Brevo Transactional API (`send_method: brevo_api`)
- **Per-industry HTML** from `industries.json` (`partnership_*.html` or `funding_intro*.html`)
- NVIDIA praise line when `use_nvidia_praise` is true for that industry
- Reserved send slots for `company_secretary_firms`, `tax_consultants`, `wealth_managers` (see `reserved_send_by_industry`)
- 5 second delay between sends; 20 second cooldown per domain
- One email per company per run (best address preferred: `info@`, `contact@`, …)
- `emails_per_run` caps each execution (default **32**)

---

## Bulk CA email harvest (separate database)

Import **hundreds or thousands** of CA emails with **resume on each run**. Uses `data/db/ca_bulk.db` — does **not** modify `immigration.db`.

### Quick start (Windows)

Double-click **`run_ca_bulk_import.bat`**. It will:

1. Create `ca_bulk_config.json` from example if missing
2. Require `ca_connect_credentials.json` (CA Connect login for profile pages)
3. Seed city queue + import existing `data/ca_connect_results.json` on first run
4. Enrich profiles until **1000 new emails** saved (configurable), resuming pending rows next time

| Batch file | Action |
|------------|--------|
| `run_ca_bulk_import.bat` | Setup + enrich (resume each run) |
| `ca_bulk_status.bat` | Pending / email counts only |
| `ca_bulk_import.bat` | CLI pass-through, e.g. `ca_bulk_import.bat export-csv` |

### Manual commands

```powershell
copy ca_bulk_config.example.json ca_bulk_config.json
copy ca_connect_credentials.example.json ca_connect_credentials.json   # add login

python ca_bulk_import.py seed-searches     # load search_queue (20 cities in example)
python ca_bulk_import.py import-json       # bootstrap from ca_connect_results.json
python ca_bulk_import.py status            # pending / with email / per city
python ca_bulk_import.py run                 # up to 1000 NEW emails (default)
python ca_bulk_import.py run --goal 500 --batch 100
python ca_bulk_import.py export-csv          # → data/ca_bulk_emails.csv
```

### Config — `ca_bulk_config.json`

| Field | Default | Purpose |
|-------|---------|---------|
| `goal_emails_per_run` | 1000 | Stop after this many **new** emails saved in one run |
| `profiles_per_batch` | 50 | Profiles per browser session batch |
| `delay_between_profiles_sec` | 1.5 | Pause between profile page loads |
| `search_queue` | 20 cities | City/state rows to harvest in order |
| `headless` | true | Hide browser during enrichment |

### While running

```
PROGRESS | CA Connect 3/50 | NAME | email@example.com
Batch done — 12 new email(s) this batch | run total 45 / 1000
```

Logs: `logs/ca_bulk_*.log`

### Scale

Each city search yields ~300–600 listing cards. With 20 cities in the example queue, potential **6,000–12,000** contacts. Add more rows to `search_queue` for wider coverage.

---

## Logs

| Log | Path |
|-----|------|
| Partnership pipeline | `logs/immigration_YYYY-MM-DD_HH-MM-SS.log` |
| Bulk CA import | `logs/ca_bulk_YYYY-MM-DD_HH-MM-SS.log` |

Live terminal lines prefixed with **`PROGRESS |`** show scrape/send/bulk counters during runs.

---

## Troubleshooting

| Issue | What to try |
|-------|-------------|
| Browser does not open | `playwright install chromium firefox` |
| Chrome fails | `--browser firefox` |
| Google consent / CAPTCHA | Run visible browser (`--no-headless`); complete challenge once |
| `GOOGLE BLOCK / CHALLENGE DETECTED` | Same as above; or wait 30–60 min and retry |
| Repeated `Found 0 unique result(s)` | Google blocking — use visible browser |
| NVIDIA timeout | Keys rotate automatically; run again |
| Brevo send failed | Check `mail_config.json` API key and verified sender |
| No emails on company site | Normal; pipeline moves on |
| CA Connect `imported 0 new email(s)` | Emails already in DB from prior run (dedup) |
| Wealth managers missing from summary | Run `seed-keywords --industry wealth_managers`; reserved industries show as `0 / 0` |
| Bulk CA login failed | Fill `ca_connect_credentials.json` |
| Bulk CA `0 new email(s)` but profiles run | Those emails already in `ca_bulk.db` |
| Want more emails per run | Raise `emails_per_run` or `run --send-limit N` |

---

## Related projects

| Path | Used for |
|------|----------|
| `..\CVL-ScraperLinkedIn_SendMails\` | SMTP patterns, SQLite email tracking, Playwright setup |
| `..\EmailJson\` | Gmail app-password profiles |
| `..\nvidia_keys\` | Rotating NVIDIA NIM API keys |

---

## Example workflows

### Partnership pipeline

```powershell
python immigration_pipeline.py seed-keywords --all --no-nvidia
python immigration_pipeline.py scrape --max-companies 20
python immigration_pipeline.py status
python immigration_pipeline.py send --dry-run --limit 3
python immigration_pipeline.py run
```

Or double-click `send_partnership_emails.bat`.

### Bulk CA harvest

```powershell
copy ca_bulk_config.example.json ca_bulk_config.json
notepad ca_connect_credentials.json
run_ca_bulk_import.bat
ca_bulk_status.bat
python ca_bulk_import.py export-csv
```

Re-run `run_ca_bulk_import.bat` anytime — it continues from pending profiles.
