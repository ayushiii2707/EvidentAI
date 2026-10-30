# AcmePay Release Notes (v3.0)

> SYNTHETIC BENCHMARK DOCUMENT. AcmePay is a fictional product created for the
> EvidentAI controlled evaluation corpus. It does not describe any real service.

## Version 3.0

Released 2026-09-15. Version 3.0 is a major release with breaking changes. The v2 API
remains available until 2027-09-15.

### Breaking Changes

- Server-side authentication uses OAuth 2.0 client credentials. Secret API keys are
  not accepted by the v3 API.
- Restricted keys are replaced by OAuth scopes.
- The charges resource is replaced by the payments resource. `POST /charges` becomes
  `POST /payments`.
- The `capture` boolean parameter is replaced by `capture_method`, which accepts
  `automatic` or `manual`.
- The `source` parameter is renamed to `payment_method`.
- The `Idempotency-Key` header is required on all POST requests.

### Changed

- Access tokens are valid for 30 minutes.

### Deprecated

- Webhook signature scheme `v1` (HMAC-SHA1) is deprecated and will be removed in
  version 3.2. Use scheme `v2` (HMAC-SHA256).

### Added

- New error code `idempotency_conflict` with status `409`.
- New error code `idempotency_key_missing` with status `400`.

## Version 2.4

Released 2026-03-02.

### Added

- Restricted keys now support the `refunds:write` permission.
- List endpoints accept the `starting_after` parameter for cursor pagination.
