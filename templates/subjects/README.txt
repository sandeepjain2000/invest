Subject line templates — one file per body HTML template in templates/bodies/.

Naming: {body_template_stem}.subject.txt
  templates/bodies/partnership_coaching.html  ->  templates/subjects/partnership_coaching.subject.txt
  templates/bodies/funding_intro.html         ->  templates/subjects/funding_intro.subject.txt

Edit the first line only. Include the recipient suffix in the template using placeholders:
  Partnership verticals:  ... with {{Domain}}
  CA / investor intro:    ... with {{RecipientIdentity}}
  College outreach:       ... with {{CollegeName}}

Placeholders (filled when sending):
  {{RecipientCompany}}   company or college name as stored
  {{Domain}}             website domain (partnership emails)
  {{RecipientIdentity}}  meaningful company/CA name, or domain if generic
  {{CollegeName}}        college name (AISHE project)

industries.json email_subject is fallback only if the .subject.txt file is missing.
