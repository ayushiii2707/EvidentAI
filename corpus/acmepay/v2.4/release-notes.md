# AcmePay Release Notes (v2.4)

> SYNTHETIC BENCHMARK DOCUMENT. AcmePay is a fictional product created for the
> EvidentAI controlled evaluation corpus. It does not describe any real service.

## Version 2.4

Released 2026-03-02.

### Added

- Restricted keys now support the `refunds:write` permission.
- List endpoints accept the `starting_after` parameter for cursor pagination.

### Changed

- The default expiry window for rolled API keys increased from 12 hours to 24 hours.

### Fixed

- Webhook retries no longer resend events that were acknowledged with status `200`.

## Version 2.3

Released 2025-11-14.

### Added

- Test-mode rate limit raised to 25 requests per second.
