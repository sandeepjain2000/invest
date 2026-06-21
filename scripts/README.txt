Windows launchers — each script cd's to the project root before running Python.

Partnership pipeline:
  send_partnership_emails.bat       Scrape (Google) then send
  send_partnership_emails_only.bat  Send only (no scrape)

CA bulk harvest:
  run_ca_bulk_import.bat            Full setup + enrich (resume each run)
  ca_bulk_status.bat                Pending / email counts
  ca_bulk_import.bat                CLI pass-through (status, export-csv, …)

Double-click any .bat here, or from a terminal:
  scripts\send_partnership_emails_only.bat
