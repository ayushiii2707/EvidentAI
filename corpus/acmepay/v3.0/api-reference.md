# AcmePay API Reference (v3.0)

> SYNTHETIC BENCHMARK DOCUMENT. AcmePay is a fictional product created for the
> EvidentAI controlled evaluation corpus. It does not describe any real service.

## Base URL

All endpoints in this reference are relative to `https://api.acmepay.example/v3`.
Request bodies are JSON encoded and must set `Content-Type: application/json`.

## Payments

A payment represents a single attempt to move money from a payment method to your
AcmePay balance. Payments replace the charges resource from v2.

### Create a Payment

`POST /payments`

Creates a new payment. By default the payment is authorized and captured immediately.

| Parameter | Type | Required | Description |
|---|---|---|---|
| amount | integer | yes | Amount in the smallest currency unit |
| currency | string | yes | Three-letter ISO currency code |
| payment_method | string | yes | Payment method token from tokenization |
| capture_method | string | no | Either automatic or manual. Defaults to automatic |
| description | string | no | Free text shown in the Dashboard |

```
curl https://api.acmepay.example/v3/payments \
  -H "Authorization: Bearer EXAMPLE_ACCESS_TOKEN" \
  -H "Idempotency-Key: 5c1f0a3e-order-1042" \
  -d '{"amount": 2000, "currency": "usd", "payment_method": "tok_visa",
       "capture_method": "manual"}'
```

### Retrieve a Payment

`GET /payments/{id}`

Returns the payment object for the given identifier. Payment identifiers begin with
`pay_`.

### Capture a Payment

`POST /payments/{id}/capture`

Captures a payment that was created with `capture_method` set to manual. Uncaptured
payments expire after 7 days.

## Refunds

### Create a Refund

`POST /refunds`

Refunds a captured payment in full or in part. The `payment` parameter is required.
Partial refunds may be issued until the total refunded amount equals the payment
amount.

## Idempotency

The `Idempotency-Key` header is required on all POST requests. Requests without it
receive status `400` with error code `idempotency_key_missing`. AcmePay stores the
result of the first request with a given key for 24 hours and returns the same result
for retries. Keys may be up to 255 characters long. Reusing a key with different
request parameters returns status `409` with error code `idempotency_conflict`.

## Pagination

List endpoints return at most 100 objects per page. Use the `starting_after`
parameter with the identifier of the last object on the current page to request the
next page. The `has_more` field indicates whether more objects are available.

## Rate Limits

The API allows up to 50 requests per second per OAuth client in live mode and 25
requests per second in test mode. Requests above the limit receive status `429`.
The `X-RateLimit-Remaining` header reports the number of requests left in the
current one-second window.

## Errors

| HTTP status | Error code | Meaning |
|---|---|---|
| 400 | parameter_missing | A required parameter was not provided |
| 400 | idempotency_key_missing | A POST request did not include an Idempotency-Key |
| 402 | card_declined | The payment method was declined |
| 404 | resource_missing | The requested object does not exist |
| 409 | idempotency_conflict | An Idempotency-Key was reused with different parameters |
| 429 | rate_limited | Too many requests |
