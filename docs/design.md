# Design

## The problem

Bills, renewals and tax deadlines all arrive by email, mixed in with newsletters,
receipts and offers. Handing the whole inbox to an AI assistant and asking "what do I owe?"
looks like the answer, and it creates two new problems:

- **Exposure.** An assistant that reads every message also reads one-time passcodes, password
  reset links and medical results. Anything in the model's context can resurface in a summary or
  a push notification.
- **Plausible mistakes.** Prose is ambiguous. An offer quotes a price and an end date; a bill
  quotes last month's total next to this month's. A reader that is usually right will, sooner or
  later, report the wrong amount or the wrong day, and a wrong due date is worse than none.

`inbox-radar` keeps the judgement in code. Python fetches the mail, per-issuer rules turn each
bill into typed fields, a schedule decides when a reminder is worth sending, an auditor checks
every outgoing message, and changes to the mailbox happen only through a hashed plan approved
with a one-time code. Whatever runs on top — a script, a dashboard, an agent — works from those
fields and never needs the mail itself.

## The pipeline

```
 mailbox ──► source ──► extract ──► triage ──► store ──► radar ──► notice ──► veto ──► notifier
 (Gmail or    (bodies   (rules →    (urgent /   (SQLite,   (schedule, (template   (audit;    (stdout,
  a local      only for  typed       important / one row    threshold, of typed     blocks     file, or
  fixture)     ruled     fields)     ignore)     per mail)  quiet days) fields)    on a hit)  your own)
               senders)

 plan ──► freeze (SHA-256) ──► one-time code ──► redeem (atomic) ──► execute: label / archive only
```

| Stage | Input | Output | Never |
|---|---|---|---|
| `source` | a mailbox | full messages from senders that have a rule; for the rest only the sender and list headers, not even the subject | downloads bodies it has no rule for |
| `extract` | one message and its PDF text | an `Obligation`: issuer, concept, amount, currency, due date, last four digits, confidence | puts message text in any field |
| `triage` | sender, headers, and what `extract` found | `urgent`, `important` or `ignore` | asks a model; promotes when unsure |
| `store` | obligations | one row per message id; a reminder log with one row per (obligation, step) | keeps a body |
| `radar` | the store and today's date | at most one notice per obligation per step, or nothing | sends a step twice, even from two runs at once |
| `veto` | a finished notice | the notice, or a refusal | sends a notice after a hit |
| `plans` | a list of mailbox actions | a frozen plan, a code, an execution log | deletes; reports spam; runs a plan that differs from the one approved |

## Principles

1. **Typed output.** `extract` emits `Decimal` amounts, `date` objects, concepts from a fixed
   list, and at most the four digits an issuer already shows. A notice is assembled from those
   values with a template. Since no field can carry free text, a passcode in a message body has
   nothing to ride on; the veto exists for the day that assumption breaks.
2. **Strict outbound, lenient inbound.** If the veto objects, the notice is dropped and the
   reason is logged. If a message cannot be parsed, it is stored with low confidence and listed
   for review rather than filled in with a guess.
3. **Asymmetric errors.** Triage resolves doubt towards the quieter bucket, and extraction
   resolves doubt towards "no obligation": an alert stream with false alarms trains people to
   stop reading it, while a missed newsletter costs nothing. Two deliberate exceptions: a message
   from a known issuer that looks like a bill but cannot be parsed is `important`, because it
   needs a human look; and mail from a sender that looks like a person (a non-role address that
   contains part of a first-and-last display name) is `important`. That second rule is a
   heuristic — spam dressed as a person passes it, and filtering spam is the mail provider's job.
4. **Quiet by default.** A run that finds nothing due within a week and no large charge produces
   no output at all.
5. **No destructive verbs.** The executor knows `label` and `archive`. Deletion and spam reports
   are not implemented, and system labels (`TRASH`, `SPAM`, `INBOX` and the rest) are refused as
   label names, because applying one is a deletion or a report by another name. No approval can
   trigger either.
6. **Reproducible.** The same mailbox always yields the same obligations, so a change that alters
   what is extracted fails a test.

## Extraction

An issuer is described by a rule: where its mail comes from, what it bills, how it writes
numbers, which phrases introduce the amount and the due date, and which phrases mark a message as
a bill rather than news from the same company.

```json
{
  "issuer": "Copperline Bank",
  "senders": ["statements@copperline.example"],
  "concept": "card statement",
  "currency": "USD",
  "number_format": "1,234.56",
  "amount_anchors": ["new balance"],
  "amount_confirm": ["payments and credits"],
  "date_anchors": ["payment due date"],
  "signal": ["statement", "new balance"]
}
```

- **Anchored values.** A value counts only when it is written as money (a currency mark directly
  before it, or a code or euro sign directly after it) and sits on the anchor's line — or on the
  next line, when the anchor is a label above its value. An anchor with nothing next to it does
  not borrow a figure from further down, and negative figures (credits) are never amounts owed.
- **Confirm phrases.** An optional confirm phrase must appear just before the anchor — the label
  of the section it belongs to — which rules out a lookalike block, such as a quoted earlier bill
  or a banner, that copies the anchor but not the section around it. Rule phrases are matched
  without regard to case or accents.
- **Signal phrases.** Mail from a billing address that lacks the rule's signal phrases is
  treated as correspondence, not as a bill.
- **`no_date_means_now`.** Some past-due notices state the amount and no date, because the date
  has passed. A rule can say so, and the obligation is then due on the day the notice was sent.
- **Explicit number formats.** Each rule declares `1,234.56` or `1.234,56`. A figure that does
  not fit the declared format is rejected rather than reinterpreted, so a thousands separator can
  never become a decimal point. Only ASCII digits are read (a digit from another script can look
  like a different one), a figure has at most nine integer digits (a card number written as money
  is not a bill), and `CA$`, `MX$` or `R$` are not dollars.
- **Languages.** Phrases, role words (*billing*, *facturas*…), card-suffix wording and ID shapes
  are matched in English and Spanish, so rules work on bilingual inboxes; the demo persona is
  English-only.
- **Dates** in English and Spanish — `3 June 2026`, `Jun 3, 2026`, `03/06/2026`,
  `3 de junio de 2026` — with day-first or month-first order declared per rule. Impossible dates
  are discarded.
- **Card charges** are recorded as movements, not obligations. A charge at or above a
  per-currency threshold produces a notice on the next run, if it arrived within the last two
  days; a first run does not alert on last month's history.

## Reminder schedule

Each obligation has four reminder steps: one week, three days, one day, and the due date. A run
sends the most recent step it has reached — four days before the due date that is the one-week
step, and the notice states the real distance ("due in 4 days"). Earlier steps it missed while
the tool was not running are recorded as skipped, never sent late. Past the due date, a bill
that did not get its due-date notice gets one overdue notice; a bill more than a month overdue
is no longer announced but stays open, and the `status` command lists it.

A step is claimed in the database before its notice is handed to the notifier, and the claim is
an atomic insert on (obligation, step): a second run, concurrent or not, cannot claim the same
step. The notifier confirms delivery; a failed delivery releases the claim so the next run tries
again; a claim left behind by a run that crashed expires after an hour, so a reminder is retried
rather than lost. Two emails about the same bill (a statement and a reminder) are one
obligation, and past-due notices for the same amount within a month of each other are one too.

## The veto

Notices are audited after they are built and before they are handed to the notifier. The text is
first normalised the way a reader would see it — full-width and other compatibility characters
folded, digits from other scripts converted, invisible characters removed, defanged links such
as `evil[.]com` rejoined — and digit runs are read through up to two separator characters
(spaces, dots, dashes, slashes, underscores, commas). Only money written exactly as the notice
template writes it (`$1,234.56`, `1.234,56 €`) and a card's last four after "ending" are exempt
from the digit checks; a figure in any other shape is audited like any other digits. The auditor
then blocks a notice that contains:

- something that looks like a passcode — four to eight digits, or a short token mixing letters
  and digits, near *code*, *OTP*, *PIN* or *código*; or a standalone six-digit number;
- a link: a scheme, `www.`, a bare domain, an IP address or `localhost`. Notices are built from
  fields, none of which is a URL, so a link in a notice can only be one that slipped through;
- a card, account or IBAN number with more than its last four digits;
- a passport, national ID or tax number (US SSN, UK NI number, Mexican CURP, Spanish DNI/NIE, and
  generic passport shapes);
- medical detail. A notice may report that a clinic sent something, not what it said.

Issuer names, amounts, dates and four-digit card suffixes are allowed through. A blocked notice is
not "cleaned" and sent: a filter that rewrites text is a filter whose output nobody checked. It
is recorded as blocked so it is not offered again, and the log keeps the categories found, never
the text. Every field is audited too, except the issuer name and concept, which come from the
rules file the owner wrote: a pharmacy whose name contains "Prescription" must still be able to
send a payment reminder.

The patterns are a list, and a list can be incomplete: a passcode spelled out in words, or a
medical term the list does not know, gets through. That is why the first defence is that notices
are built from typed fields, and the veto is the second.

## Plans and one-time codes

Bulk changes — label every message from a newsletter, archive a sender's backlog — need consent,
and per-message consent does not scale. Consent is therefore given to a **plan**: the full list
of actions grouped by sender and label, with counts. Senders must be an address or a domain and
labels a short name; both are passed through the veto before the plan is accepted, because the
summary that carries them goes out through the notifier. The plan is serialised in a canonical form
and hashed with SHA-256, and a one-time code is issued for that hash: four characters from an
alphabet with no ambiguous glyphs, valid ten minutes, single use. Redeeming the code is an atomic
file rename, so two processes cannot both redeem it. Before executing, the executor hashes the
plan again; a mismatch means the plan changed after it was shown, and nothing runs — and the
code is spent either way. Five wrong codes in a row void every pending code.

The code travels through the notifier, to the person. The function that proposes a plan returns
only its hash, so a script or an agent that asks for approval does not receive the code it would
need to grant it. Two conditions keep that true: the notifier is configured by the owner, not
chosen by the caller, and the caller does not run as the same operating-system user — pending
codes live in a private (0700) folder, which protects them from other users, not from the owner's
own processes.

Consent is the code, not a word. Text such as "approved" can arrive inside an email or a
forwarded message; a code that was only ever displayed to the user cannot.

## The fictional mailbox

The tests and the demo use a generated mailbox for an invented household that pays every bill in
US dollars and reads its mail in English. Every address uses the reserved `.example` domain,
every company and person is invented, amounts are placeholder figures (`$1,234.56`, `$300.00`)
or come from a seeded generator, and dates are relative to the day it runs. Other number formats,
day-first dates and other languages are covered by unit tests, not by the mailbox.

| Case | What it tests |
|---|---|
| Card statement whose figures are only in a PDF | attachment extraction |
| Statement listing a balance and a minimum payment | the anchor picks the balance, not the first figure |
| Loan statement listing the remaining principal before the installment | a decoy figure is skipped; a day-first date in words |
| Bill that quotes last month's amount before this month's | a section anchor skips the earlier figure |
| Insurance premium with a labelled due date | the common case: one amount, one date |
| Offer from a company that also bills you | signal phrases |
| Dozens of small card charges and one large one | movements and the alert threshold |
| Past-due notice with an amount and no date | `no_date_means_now` |
| Passcodes, a reset link and a full account number | the veto |
| Clinic notification with (synthetic) lab values | the veto's medical rule |
| Notices due in one and two months | stored, silent until the schedule reaches them |
| A message addressed to "any assistant" | stays data; nothing changes |
| Newsletters and promotions | the bulk of the inbox; `ignore` |

## Bounds on hostile input

Everything a message contains is attacker-controlled, so every stage has a ceiling: a body is cut
at 200,000 characters and an HTML body at 400,000 before it is reduced to text (in one linear
pass, never a backtracking pattern); `pdftotext` output is read up to 256 KB and the process is
then stopped, however much a small compressed PDF would expand to; MIME nesting is walked 30 levels
deep; a malformed record in a fixture mailbox is skipped and counted, never echoed; an attachment
path is resolved with symlinks and must stay inside the mailbox folder.

## Out of scope

- **Delivery channels.** The notifier is an interface. Standard output and a JSONL file are
  included; a messaging app is a small adapter on the same interface.
- **Model access to mail.** An agent can be given the store, the notices and the plan tools. It
  has no reason to see a message.
- **Acting on bills.** No payments, no unsubscribing, no deletion. The tool reminds; the person
  pays.
