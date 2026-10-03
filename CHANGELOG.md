# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- The veto also blocks UK National Insurance numbers and Mexican CURPs.

### Changed
- The fictional mailbox and example rules describe a new invented household that pays in US
  dollars; the example card-charge alert threshold is $250.

## [0.1.0] - 2026-10-01

### Added
- Initial public release.

### Security
- Sender addresses are parsed strictly instead of with `email.utils.parseaddr`, whose
  reading of ambiguous From headers varies across Python versions (CVE-2023-27043).
