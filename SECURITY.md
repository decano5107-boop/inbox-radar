# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Use GitHub's private vulnerability reporting on this repository:
**Security → Report a vulnerability** (<https://github.com/decano5107-boop/inbox-radar/security/advisories/new>). It creates a private thread visible only to the maintainer.

This is maintained by one person, so treat these as targets rather than guarantees: an
acknowledgement within 7 days, and a status update within 30.

Disclosure is coordinated: once a fix is released, the advisory is published with credit to the
reporter, unless they prefer to stay anonymous.

## Scope

This project runs locally as developer tooling. The most relevant risks are:

- Anything that gets a passcode, a link, a full card or account number, a government ID or
  medical detail through the veto to a notifier
- Any way to change the mailbox without the one-time code issued for that exact plan, or to
  make a plan delete, trash, report spam or send mail
- Message text reaching the database, the logs or command output
- A reminder sent twice, sent late, or lost after a failed delivery

## Supported versions

The latest released minor version receives fixes. Older versions do not.
