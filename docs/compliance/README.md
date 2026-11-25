# Compliance templates for the site owner

These pages help you, the person who runs a Reach Around site, keep the
paperwork that privacy rules such as the GDPR commonly ask a controller to
keep. They are fill-in templates, pre-filled with what the software does today.

This is an engineering write-up, not legal advice. It says what the code
stores, where it sends it and how long it keeps it. It does not say what the
law requires of you. Which rules apply depends on who you are, where you live
and where your visitors live. Have a qualified lawyer check the finished
documents before you rely on them.

## The templates

| Page | What it is | When to use it |
|---|---|---|
| [records-of-processing.md](records-of-processing.md) | A record of each processing activity: data, purpose, legal basis, recipients, transfers, retention, security | Fill in once, review on every change |
| [dpia-screening.md](dpia-screening.md) | A checklist that asks whether a full data protection impact assessment is needed, with the answers the software gives by default | Fill in once, re-run on every trigger it lists |
| [breach-procedure.md](breach-procedure.md) | A short step list for a personal data breach: detect, contain, assess, notify, record | Fill in the contacts now, before you need it |

## How to read the markers

- **Code today** means the behavior exists in the repository now. Check it
  against your deployed version before you rely on it.
- **PLANNED** means the privacy baseline item is tracked but not built yet.
  Do not describe a planned control as live in anything you publish or send
  to a regulator.
- **OWNER** marks a blank only you can fill in: your name, contact, regulator,
  and which optional features you switched on.
- **LAWYER** marks a question the software cannot answer.

## Keep these in sync with the site

The records are only useful while they match the site. Re-read them when you
change `site.yaml` (`features`, `analytics`, `privacy`, `alerts`), when you
switch a feature on, and when you add a service that receives visitor data.
The public privacy page is generated from the site config; see
[privacy.md](../privacy.md) for what it covers and how lead retention and
deletion requests work, and [audience-analysis-privacy.md](../audience-analysis-privacy.md)
for the do-not-collect list.

Keep your filled-in copies private (not in a public repository), because they
name your contacts and processors.
