# AcmePay API Reference (v2.4)

> SYNTHETIC BENCHMARK DOCUMENT. AcmePay is a fictional product created for the
> EvidentAI controlled evaluation corpus. It does not describe any real service.

## Base URL

All endpoints in this reference are relative to `https://api.acmepay.example/v2`.
Request bodies are JSON encoded and must set `Content-Type: application/json`.

## Charges

A charge represents a single attempt to move money from a payment method to your
AcmePay balance.

### Create a Charge

`POST /charges`

Creates a new charge. By default the charge is authorized and captured immediately.

| Parameter | Type | Required | Description |
|---|---|---|---|
| amount | integer | yes | Amount in the smallest currency unit |
| currency | string | yes | Three-letter ISO currency code |
| source | string | yes | Payment method token from tokenization |
| capture | boolean | no | Whether to capture immediately. Defaults to true |
| description | string | no | Free text shown in the Dashboard |

```
curl https://api.acmepay.example/v2/charges \
  -H "Authorization: Bearer sk_test_EXAMPLEKEY000000" \
  -d '{"amount": 2000, "currency": "usd", "source": "tok_visa", "capture": false}'
```

### Retrieve a Charge

`GET /charges/{id}`

Returns the charge object for the given identifier. Charge identifiers begin with
`ch_`.

### Capture a Charge

`POST /charges/{id}/capture`

Captures a charge that was created with `capture` set to false. Uncaptured charges
expire after 7 days.

## Refunds

### Create a Refund

`POST /refunds`

Refunds a captured charge in full or in part. The `charge` parameter is required.
Partial refunds may be issued until the total refunded amount equals the charge
amount.

## Idempotency

The `Idempotency-Key` header is optional on all POST requests. When provided, AcmePay
stores the result of the first request with that key for 24 hours and returns the
same result for retries. Keys may be up to 255 characters long.

## Pagination

List endpoints return at most 100 objects per page. Use the `starting_after`
parameter with the identifier of the last object on the current page to request the
next page. The `has_more` field indicates whether more objects are available.

## Rate Limits

The API allows up to 100 requests per second per account in live mode and 25
requests per second in test mode. Requests above the limit receive status `429`.
The `X-RateLimit-Remaining` header reports the number of requests left in the
current one-second window.

## Errors

| HTTP status | Error code | Meaning |
|---|---|---|
| 400 | parameter_missing | A required parameter was not provided |
| 402 | card_declined | The payment method was declined |
| 404 | resource_missing | The requested object does not exist |
| 429 | rate_limited | Too many requests |
