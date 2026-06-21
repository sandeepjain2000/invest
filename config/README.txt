Project configuration JSON files.

  sender_config.json              Pipeline limits, sender identity, template defaults
  mail_config.json                Brevo API key + verified sender (gitignored)
  mail_config.example.json        Template for mail_config.json
  industries.json                 15 verticals, template_file names, seed queries
  ca_bulk_config.json             Bulk CA harvest settings (gitignored)
  ca_bulk_config.example.json
  ca_connect_credentials.json       CA Connect login (gitignored)
  ca_connect_credentials.example.json

Copy *.example.json to the matching file and fill in secrets.

Override paths via env: MAIL_CONFIG_FILE, CA_CONNECT_CREDENTIALS_FILE
