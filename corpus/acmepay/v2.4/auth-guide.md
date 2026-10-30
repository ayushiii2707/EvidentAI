# AcmePay Authentication Guide (v2.4)

> SYNTHETIC BENCHMARK DOCUMENT. AcmePay is a fictional product created for the
> EvidentAI controlled evaluation corpus. It does not describe any real service.

This guide explains how server-side integrations authenticate with the AcmePay API
version 2.4. All requests must be made over HTTPS to the base URL
`https://api.acmepay.example/v2`. Requests made over plain HTTP are rejected with
status `403`.

## Overview

AcmePay v2.4 supports two credential types: secret API keys for server-to-server
calls, and publishable keys for client-side tokenization. Secret keys grant full
access to the account and must never be embedded in mobile apps or browser code.

Every account has one live secret key and one test secret key. Test keys begin with
`sk_test_` and live keys begin with `sk_live_`. Objects created with a test key are
not visible to live keys.

## API Keys

### Creating API Keys

Administrators create keys in the Dashboard under Settings, Developers, API Keys.
A newly created secret key is displayed once. If the key is lost, it must be rolled
and replaced; AcmePay cannot recover it.

Each account may hold at most five active secret keys at the same time. Creating a
sixth key fails until an existing key is revoked.

### Sending API Keys

Send the secret key in the `Authorization` header using the Bearer scheme:

```
curl https://api.acmepay.example/v2/charges \
  -H "Authorization: Bearer sk_test_EXAMPLEKEY000000"
```

Keys sent as query parameters are rejected with status `401` and error code
`key_in_query`. AcmePay does not accept HTTP Basic authentication.

### Rolling API Keys

Rolling a key creates a replacement and schedules the old key for expiry. The default
expiry window for the old key is 24 hours and can be set between 0 and 7 days.
During the window both keys are accepted.

## Restricted Keys

Restricted keys limit access to specific resources. A restricted key with only the
`charges:write` permission can create charges but cannot list customers or issue
refunds. Restricted keys begin with `rk_live_` or `rk_test_`.

Restricted keys are recommended for any service that only needs a subset of the API,
such as a checkout service that only creates charges.

## Webhook Signatures

AcmePay signs every webhook event it sends to your endpoint. The signature is
delivered in the `AcmePay-Signature` header and uses signature scheme `v1`.

### Verifying Signatures

The `v1` scheme is an HMAC-SHA1 of the string `timestamp.payload`, keyed with the
webhook signing secret that begins with `whsec_`. Compute the expected signature and
compare it with a constant-time comparison function.

Reject events whose timestamp is more than 300 seconds older than the current time to
protect against replay attacks.

### Rotating Signing Secrets

Signing secrets can be rotated from the Dashboard. After rotation, AcmePay signs
events with both the old and the new secret for 24 hours.

## Error Handling

Authentication errors use the following codes.

| HTTP status | Error code | Meaning |
|---|---|---|
| 401 | invalid_api_key | The key does not exist or was revoked |
| 401 | key_in_query | The key was sent as a query parameter |
| 403 | insecure_transport | The request was not made over HTTPS |
| 403 | insufficient_permissions | A restricted key lacks the required permission |

When a request fails with `invalid_api_key`, do not retry automatically. Check that
the key was not rolled or revoked, and that test and live keys are not mixed.

## Troubleshooting

### Requests Fail With 401 After a Key Roll

If requests start failing with `invalid_api_key` roughly 24 hours after a key roll,
the old key has expired. Update every service that still uses the old key. Search
deployment configuration for the `sk_live_` prefix to find remaining references.

### Webhook Signature Mismatch

Signature mismatches are usually caused by computing the HMAC over a parsed and
re-serialized JSON body. Always use the raw request body exactly as received.
