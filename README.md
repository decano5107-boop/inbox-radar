# Inbox Radar

[![tests](https://github.com/decano5107-boop/inbox-radar/actions/workflows/tests.yml/badge.svg)](https://github.com/decano5107-boop/inbox-radar/actions/workflows/tests.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Know what you owe and when, from your inbox, without giving anything — person or model — the
mail itself.**

Bills, payment reminders and tax deadlines arrive by email and get lost there. Inbox Radar reads
them with per-issuer rules and turns each into typed fields: issuer, amount, currency, due date,
the last four digits of the card. It reminds you a week, three days and a day before each due
date and on the day itself, once each, sends one notice if a due date passes unannounced, and is
silent on days with nothing to say. Every outgoing message is audited
for passcodes, links, account numbers, ID numbers and medical detail, and blocked on any hit.
Bulk changes to the mailbox — labelling, archiving — run only as a plan approved with a one-time
code bound to that plan's hash. There is no delete.

It is built to sit under an AI assistant: the assistant gets fields and plans, never the inbox.
Plain Python, standard library only, no model calls. An excerpt from the demo below (dates
follow the day you run it):

```
Thu 01 Oct
📱 Skylark Mobile — phone bill: $54.32, due tomorrow (Fri 02 Oct).
📱 Clearbrook Water — water bill: $23.45, due in 2 days (Sat 03 Oct).
📱 Copperline Bank — card statement: $1,234.56, due in 5 days (Tue 06 Oct), ending in 1234.
Fri 02 Oct
📱 Skylark Mobile — phone bill: $54.32, due today.
📱 Clearbrook Water — water bill: $23.45, due tomorrow (Sat 03 Oct).
📱 Harborstone Insurance — insurance premium: $300.00, due in 7 days (Fri 09 Oct).
```

## Try it first

The demo generates a fictional mailbox (187 messages for an invented household that pays its
bills in US dollars, every address on the reserved `.example` domain), scans it, walks the reminder schedule over two
weeks, shows the veto blocking a passcode, and approves a bulk plan with a one-time code. It
writes only to a new temporary folder.

```bash
git clone https://github.com/decano5107-boop/inbox-radar.git
cd inbox-radar
python3 examples/demo.py
```

Requirements: Python 3.11 or newer. `pdftotext` (from poppler: `brew install poppler` or
`apt install poppler-utils`) is needed to read bills whose figures are only in a PDF; without it,
those bills are listed for review instead of read.

## How it decides

| Stage | What it does |
|---|---|
| **Source** | Gmail (read-only token) or a folder of messages. Bodies and attachments are fetched only for senders that have a rule; everything else arrives as headers. |
| **Extract** | A rule per issuer: sender addresses, number format (`1,234.56` or `1.234,56`), day/month order, the phrases that introduce the amount and the due date. A value must be written as money and sit next to its anchor phrase; a past-due notice with no date can be treated as due now. |
| **Triage** | `urgent` (past-due notices from rules marked `no_date_means_now`, and large card charges), `important` (other bills, people, senders you list), `ignore` (everything else). When unsure, the quieter bucket. |
| **Store** | SQLite, private file. Fields only; no message text is ever stored. |
| **Radar** | Reminder steps at 7, 3, 1 and 0 days; missed steps are skipped, not sent late; one overdue notice; large charges once. Each step is claimed in the database, so two runs cannot both send it. |
| **Veto** | Normalises the text, then blocks passcodes, links, card/account/IBAN numbers, ID numbers and medical detail. A blocked notice is never sent and never retried; the log keeps a short id and the category, never the text. |
| **Plans** | `label` and `archive` only, never system labels such as `TRASH` or `SPAM`. A plan is hashed; a four-character code for that hash goes to you through the notifier; the code works once, for ten minutes, for that exact plan. |

The reasoning behind each choice, and what each stage must never do, is in
[docs/design.md](docs/design.md).

## Use it on your own mail

1. **Write your rules.** Copy [`examples/rules.example.json`](examples/rules.example.json) and
   replace its fictional issuers with yours. For each issuer, open one real bill and copy the
   phrase that sits right before the amount and the one before the due date. Amounts are read in
   US dollars and euros. `triage.important`
   lists addresses or domains you always want to see; `thresholds` sets the card-charge alert per
   currency.
2. **Get a read-only Gmail token.** Create an OAuth client of type *Desktop app* in your own
   Google Cloud project with the Gmail API enabled, then, in a virtual environment:

   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   python3 -m pip install google-api-python-client google-auth google-auth-oauthlib
   python3 examples/gmail_token.py client_secret.json token.json
   ```

3. **Scan, then remind.** Run both from a scheduled job (cron or launchd), for example every
   morning:

   ```bash
   python3 -m inbox_radar scan --rules rules.json --gmail token.json
   python3 -m inbox_radar remind --rules rules.json --notifier jsonl:out/notices.jsonl
   ```

   `remind` prints nothing on a quiet day. The notifier is `stdout` or `jsonl:PATH`; sending to
   your phone is a class with a `send(text) -> bool` method (see `inbox_radar/notify.py`).

| Command | What it does |
|---|---|
| `scan --rules F (--gmail TOKEN \| --mailbox DIR)` | Read the mailbox; store bills and charges; print counts. |
| `remind --rules F [--notifier N]` | Send today's reminders through the veto. |
| `status` | Open bills with short ids, bills overdue more than a month, bills that need review, and paid or dismissed ones. |
| `mark ID paid\|dismissed\|open` | Stop reminders for a bill, or reopen it. |
| `override SENDER urgent\|important\|ignore` | Always triage an address or domain this way. |
| `plan draft FILE --sender ADDR [--label NAME] (--gmail TOKEN \| --mailbox DIR)` | Write a plan that archives (or labels) every message from one address. |
| `plan show FILE` | Print the plan as it will be shown for approval. |
| `plan propose FILE --notifier N [--state DIR]` | Send the summary and a one-time code through the notifier. |
| `plan approve FILE --code C [--gmail-modify TOKEN] [--state DIR]` | Run the plan if the code was issued for it. Without `--gmail-modify` it is a dry run that still spends the code. |

Every command also takes `--db PATH` (default `out/radar.db`) and `--today YYYY-MM-DD`, before or
after the command name; `--state` defaults to `out/plans`. A plan file is a JSON list of actions,
each `{"verb": "archive" | "label", "sender": ADDR, "message_ids": [...], "label": NAME}`
(`label` only for the `label` verb); `plan draft` writes one for you. `propose` has no default
notifier on purpose: the code must reach you, not whoever runs the command. To apply plans to
Gmail, create a second token with `--modify` and keep it apart from the read-only one.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

The fictional mailbox doubles as a regression fixture: the generator records what it wrote into each
message, and the suite expects the pipeline to reproduce it exactly — every bucket, every bill, every
card charge. The fixture is invented, so this catches regressions; it is not an accuracy figure. The rest is derived from
what each stage must never do — read a card suffix as the amount, take a decoy figure, send a
step twice, let a passcode or a reset link through the veto, run a plan that changed after
approval, apply `TRASH`. CI runs it on Linux and macOS with Python 3.11 to 3.13.

## Limits

- **Rules are per issuer.** A new biller needs a rule; until then its mail is only triaged. That
  is the price of never guessing an amount or a date.
- **The veto works from lists.** A passcode spelled out in words, or a medical term the list does
  not know, gets through. The first defence is that notices are assembled from typed fields.
- **Looks-like-a-person is a heuristic.** Spam that imitates a person lands in `important`;
  filtering spam is the mail provider's job.
- **One user, one machine.** Pending approval codes live in a private folder, which protects them
  from other operating-system users, not from your own processes. Configure the notifier
  yourself; do not let the caller of `propose` choose it.
- **Gmail is the only live source.** Other providers need a class with the same `messages()`
  and `ids_from()` methods.
- **Dollars and euros.** Amounts are recognised by their currency mark; a rule in another
  currency is rejected when the rules file loads. Adding one means extending the currency marks
  and `money()` in `inbox_radar/text.py`, and the money pattern the veto exempts in `veto.py`.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
