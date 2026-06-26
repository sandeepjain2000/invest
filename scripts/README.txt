Windows launchers — each script cd's to the project root before running Python.

Partnership pipeline:
  send_partnership_emails.bat       Scrape (Google) then send
  send_partnership_emails_only.bat  Send only (no scrape)

CA bulk (ICAI):
  harvest_ca_from_icai.bat          Pull new CA emails from ICAI (browser)
  ca_bulk_status.bat                Check harvest progress (read-only)
  send_ca_emails.bat                Email contacts already in ca_bulk.db

Optional CLI (terminal, with arguments):
  ca_bulk_import.bat export-csv
  ca_bulk_import.bat seed-searches
  run_ca_bulk_import.bat            Old name for harvest_ca_from_icai.bat

Double-click any .bat here, or from a terminal:
  scripts\ca_bulk_status.bat
  scripts\harvest_ca_from_icai.bat
