# Webhook payload v1 and notification routing

This document is for the engineer who wires a SOAR, a ticket system or an automation platform to
the alerts this product sends. It describes what arrives at a named webhook destination: the four
events, their exact JSON bodies, the fields, the headers, how to verify the signature, and the
delivery guarantees and refusals to expect. The destinations and their rules are administered through the admin API
(`/api/v1/admin/notifications/destinations` and `/api/v1/admin/notifications/rules`) by a
principal with `admin:notifications`; this version has no `/admin/notifications` page for them.

A named destination is a webhook this site administers in the application, not a URL in `.env`.
Only a named destination receives a v1 body. The older `.env` generic webhook keeps its own body
(see [§Not v1](#the-env-generic-webhook-is-not-v1)).

## When a body is sent

One v1 body per lifecycle event and destination:

| Event | Sent when |
|---|---|
| `opened` | A new alert row is opened. A refresh of an open alert sends nothing. |
| `acknowledged` | An analyst acknowledges the alert, from `OPEN` only. |
| `resolved` | The alert is resolved from `OPEN` or `ACKNOWLEDGED`, and `NOTIFY_SEND_RECOVERY` is `true` (the default). With that setting off, no resolution event is sent. |
| `test` | An administrator chooses "send test" on the destination. It carries no alert. |

There is no `reopened` event: after a resolution the next observation of the same condition opens
a new alert row and sends `opened` with a new `alert_id`. A named destination receives an event
only when a routing rule selects it. Rules add destinations; a rule can also stop the default
channels for the alerts it matches, and it never changes a body.

## Example bodies

These are the exact bytes the builder produces for fixed inputs, one line each, with compact
separators and this key order. The bytes are the signed artifact, so verify what arrived.

`opened`:

```json
{"schema_version":1,"event":"opened","idempotency_key":"6f1c2a54-3e0b-4b7e-9d2c-8a51f0b7c3d1:OPENED:0b0c2b6e-5a76-4a53-9f2a-3f6a1d6c7e11","alert_id":"6f1c2a54-3e0b-4b7e-9d2c-8a51f0b7c3d1","fingerprint":"anomaly:093dc7406c1300eda29d3513c42e0649","family":"log_source","source_type":"log_source","source_id":"9a3d5c11-8b2f-4e6a-9c1d-2f4b6a8c0e31","severity":"HIGH","subject":"Volume drop on proxy-04","summary":"Events fell below the expected band.","status":"OPEN","occurrence_count":1,"opened_at":"2026-11-13T09:00:00Z","occurred_at":"2026-11-13T09:00:00Z","url":"https://soc.example.internal/alerts/6f1c2a54-3e0b-4b7e-9d2c-8a51f0b7c3d1","honesty":null}
```

`acknowledged`:

```json
{"schema_version":1,"event":"acknowledged","idempotency_key":"b2e4d6f8-1a3c-4e5b-8d7f-9a0b1c2d3e4f:ACKNOWLEDGED:0b0c2b6e-5a76-4a53-9f2a-3f6a1d6c7e11","alert_id":"b2e4d6f8-1a3c-4e5b-8d7f-9a0b1c2d3e4f","family":"waf","source_type":"waf_endpoint","severity":"MEDIUM","subject":"Request rate on checkout","status":"ACKNOWLEDGED","opened_at":"2026-11-13T09:00:00Z","acknowledged_at":"2026-11-13T09:14:00Z","acknowledged_by":"analyst.kaya","occurred_at":"2026-11-13T09:14:00Z","url":"https://soc.example.internal/alerts/b2e4d6f8-1a3c-4e5b-8d7f-9a0b1c2d3e4f","honesty":null}
```

`resolved`:

```json
{"schema_version":1,"event":"resolved","idempotency_key":"c3f5a7b9-2d4e-4f6a-8b1c-3d5e7f9a0b1c:RESOLVED:0b0c2b6e-5a76-4a53-9f2a-3f6a1d6c7e11","alert_id":"c3f5a7b9-2d4e-4f6a-8b1c-3d5e7f9a0b1c","family":"ips","source_type":"ips_entity","severity":"HIGH","subject":"Rare signature on the DMZ sensor","status":"RESOLVED","resolved_at":"2026-11-13T10:02:00Z","resolved_by":"ips-anomaly-engine","resolution_reason_code":"anomaly.resolution.recovered","occurred_at":"2026-11-13T10:02:00Z","url":"https://soc.example.internal/alerts/c3f5a7b9-2d4e-4f6a-8b1c-3d5e7f9a0b1c","honesty":null}
```

`test`:

```json
{"schema_version":1,"event":"test","idempotency_key":"test:0b0c2b6e-5a76-4a53-9f2a-3f6a1d6c7e11:2026-11-13T09:00:00Z","alert_id":null,"family":null,"severity":"LOW","subject":"Notification test","occurred_at":"2026-11-13T09:00:00Z","url":null,"honesty":null}
```

A time is ISO-8601 UTC with a literal `Z` suffix, never `+00:00`. Microseconds
(`2026-11-13T09:00:00.123456Z`) appear only when the stored time has them; a whole second stays
`09:00:00Z`. The `Z` form is part of the bytes, so do not reformat a time before verifying.

## Fields

`Present in` names the events that carry the key. A key that an event does not list is absent
from that body, not `null`.

| Field | Type | Present in | Null when | Meaning |
|---|---|---|---|---|
| `schema_version` | integer | every event | never | `1`. The version of the body layout on this page. |
| `event` | string | every event | never | `opened`, `acknowledged`, `resolved` or `test`. |
| `idempotency_key` | string | every event | never | `<alert_id>:<TRANSITION>:<destination_id>` for a lifecycle event, `test:<destination_id>:<occurred_at>` for a test. It is also the `Idempotency-Key` header. |
| `alert_id` | string, UUID | every event | on `test` | The alert this event is about. |
| `fingerprint` | string | `opened` | never | The alert's dedup fingerprint: the same condition keeps it across alerts. |
| `family` | string or null | every event | on `test`, or when the alert's source type has no family | One of `log_source`, `waf`, `ips`, `property_watch`, `rule`, `catchall_sender`, `custom_rule`, `scheduled_search`, `qradar_instance`. |
| `source_type` | string | `opened`, `acknowledged`, `resolved` | never | The producer that raised the alert, for example `log_source`, `waf_endpoint` or `ips_entity`. |
| `source_id` | string, UUID | `opened` | when the alert has no source object | The QRadar object the alert is about. It is an identifier, not an event value. |
| `severity` | string | every event | never | `CRITICAL`, `HIGH`, `MEDIUM` or `LOW`. A `test` is `LOW`. |
| `subject` | string | every event | never | The alert title, `Notification test` for a test. |
| `summary` | string | `opened` | never for an alert | The alert description, or the title when the description is empty. |
| `status` | string | `opened`, `acknowledged`, `resolved` | never | The alert's status at the transition: `OPEN`, `ACKNOWLEDGED` or `RESOLVED`. |
| `occurrence_count` | integer | `opened` | when the stored payload has no count | How many times the condition was seen in this alert. Never a made-up `0`. |
| `opened_at` | string, time | `opened`, `acknowledged` | when the stored payload has no opened time | When the alert opened. |
| `acknowledged_at` | string, time | `acknowledged` | never | When the alert was acknowledged. |
| `acknowledged_by` | string | `acknowledged` | when no actor is recorded | The principal that acknowledged the alert. |
| `resolved_at` | string, time | `resolved` | never | When the alert was resolved. |
| `resolved_by` | string | `resolved` | when no actor is recorded | The principal or engine that resolved the alert, for example `ips-anomaly-engine` or an analyst's user name. |
| `resolution_reason_code` | string | `resolved` | when the resolution has no coded reason, or its reason is free text | The coded resolution reason. Free text never leaves the site. |
| `occurred_at` | string, time | every event | never | The time of the event itself: the transition time, or the moment of the test. |
| `url` | string | every event | on `test` | `public_origin` + `/alerts/<alert_id>`. |
| `honesty` | null | every event | always | Reserved and always `null`: the honesty states are not alert columns. |

`url` is built from `PUBLIC_ORIGIN`. If that setting is an address only the internal network can
reach, the link in the SOAR will not open for a user elsewhere.

## Headers

Every request carries:

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |
| `Idempotency-Key` | The body's `idempotency_key`, byte for byte. |
| `Host` | The destination's configured host, with its port when the stored URL names one explicitly. |
| `X-QRadar-Dash-Signature` | `v1=<hex>`, the HMAC-SHA256 of the raw body, lowercase hex. Present only when the destination has a signing secret. |

The destination may add up to five custom headers. A name must match `^[A-Za-z0-9-]{1,64}$`, a
value is at most 4096 characters and may not contain a carriage return or line feed. `Host`,
`Content-Length`, `Content-Type`, `Idempotency-Key` and `X-QRadar-Dash-Signature` cannot be
redefined; the API refuses them. Header values are stored encrypted and the API returns their
names only.

The request is sent to the address resolved and checked for that send, while `Host` and the TLS
server name stay the configured host, so a virtual host and its certificate still match.

## Verifying the signature

Verify the bytes exactly as received, before any JSON parsing or reformatting: a parsed and
re-encoded body is different bytes and will not match. Compare in constant time.

```python
import hashlib
import hmac


def verify(raw_body: bytes, header: str, secret: str) -> bool:
    expected = "v1=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.strip())
```

A worked vector, checkable without a server: the `opened` body above, signed with the throwaway
secret `demo-shared-secret`, carries `v1=98247548768adcbaa78ca1bd1e3f37ea258f3d430ac5d3ab4f1a53e7776a4940`.
It is a made-up value for this example, not a secret of any site.

Reject a request whose signature does not match, or one whose destination has no secret and so
carries no signature header, then apply the `Idempotency-Key` check below.

## Delivery semantics

Delivery is **at least once**. A peer must expect a repeat and use the `Idempotency-Key` to drop
one it has already accepted. A repeat comes from either of two cases: the peer accepted the body
but the answer was lost, so the sender retried; or a retry was already in flight when a later
attempt succeeded.

Within this site the notification row is unique per (alert, transition, channel, target), so the
same event is never queued twice for one destination. The `dispatch_notifications` worker delivers
due rows every 30 s, at most 100 per run.

Each attempt is one POST. Redirects are never followed: a 3xx is refused and recorded. There is
no proxy layer. The request times out after 10 s, and the response body is read for at most 64 KiB
and discarded; only the status code is used. A status or failure maps to one class:

| Outcome | Class | Retried |
|---|---|---|
| 2xx | `ok` | No: the event is delivered. |
| 429, any 5xx | `http_status` | Yes |
| Any other 4xx | `http_status` | No: permanent |
| 3xx | `redirect_refused` | No: permanent |
| Timeout after 10 s | `timeout` | Yes |
| Connection, DNS or TLS failure | `transport` | Yes |
| The destination was deleted or disabled before the send | `no_destination` | No |
| The send-time URL check refused the address or a plain-HTTP URL | `ssrf_refused` | No |
| The stored payload cannot meet this contract | `payload_invalid` | No |

Retries follow the settings, not a fixed schedule. `NOTIFY_MAX_RETRIES` (default `5`) allows at
most six attempts per event. The wait before each retry is a random value between zero and the
capped exponential, `NOTIFY_RETRY_BASE_SECONDS` (default `2.0`) doubling each time and capped at
`NOTIFY_RETRY_MAX_SECONDS` (default `300.0`): up to 2, 4, 8, 16 and 32 s for the five retries at
the defaults. An event whose attempts are used up, or whose failure is permanent, becomes a dead
letter: it stays on the alert's notification history for inspection and is not sent again.

## Destination URL rules

An administrator saves a destination through the admin API. The URL is checked when it is
saved and again at every send:

- `https` only, unless the destination's `allow_http` flag is set.
- A user name, a password or a fragment in the URL is refused.
- The host is resolved. Every address it resolves to must be public, unless the destination's
  `allow_private` flag is set. Private, loopback, link-local, multicast, unspecified and reserved
  addresses are refused, a metadata address included.
- The checked address is pinned for the send: the connection goes to the address that was checked,
  so a name that changes its answer between the check and the send does not redirect the request.

Both flags default off. Each one is a deliberate exception, for an internal SOAR reached over
plain HTTP or by a private address. Leave them off unless the peer is on that internal network.

A site may hold at most 10 destinations and 50 routing rules. The API returns a destination's
scheme, host and port, whether a secret is set and its custom header names; never the path, the
secret or a header value.

## What is never sent

A v1 body is built from named alert fields only. It never contains:

- raw events, event payloads or QRadar property values;
- the alert's evidence snapshot: `observed_value`, `expected_value`, `deviation_score` and the
  evidence reason are not part of v1;
- `source_anomaly_ids`, the ids of the anomalies behind the alert;
- AQL, a search id or a console URL;
- a path, a source or destination address, or any other raw value from the event;
- any URL other than the alert link, and that link's id is the alert id;
- a resolution's free text; only the code is sent;
- the destination URL, its signing secret, its custom header values or the peer's response body.

`honesty` is always `null`, so a peer must not read a missing honesty state as "healthy". The
alert page is where the honesty states of an alert are read.

## The `.env` generic webhook is not v1

`NOTIFY_GENERIC_WEBHOOK_URL` in the production `.env` is an older channel and is unchanged. It
receives a different body, with no `schema_version`, no `event`, no idempotency key and no
signature. Its shape, with the evidence keys present only when the alert stored them:

```json
{"title":"...","body":"...","severity":"HIGH","transition":"OPENED","alert_id":"<uuid>","fingerprint":"<fp>","fields":{"occurrences":1,"first_seen":"...","observed_value":...,"expected_value":...,"deviation_score":...,"reason":"..."}}
```

That channel's URL is itself the credential and is not checked against the private-address rules.
Use a named destination for any new integration.

## Sending a test and reading the history

"Send test" on a destination posts one `test` body through the same guarded sender:
`POST /api/v1/admin/notifications/destinations/<id>/test`, under `admin:notifications`, limited by
`RATE_LIMIT_WEBHOOK_TEST` (default `5/minute`). It writes no notification row and stores no
body; the destination keeps the time, HTTP status and outcome class of the last test. The answer
is a status, a class and a sentence saying what happened. A test with no alert deliberately
proves the transport, the signature and the URL, not the routing of a real event: it bypasses
rules, so it reaches the destination even when no rule selects it.

The alert page lists each notification with its channel, transition, status, attempt count and
last error. `GET /api/v1/alerts/<id>/notifications` returns the same rows with `attempt_log`: the
newest eight attempts, each with `attempted_at`, `http_status` and `error_class`. The classes are
`ok`, `transport`, `timeout`, `http_status`, `redirect_refused`, `ssrf_refused`, `no_notifier`,
`no_destination`, `permanent` and `payload_invalid`. In that response a named destination's
`target` is the destination name, or `(deleted destination)`; a `.env` webhook's is only
`scheme://host[:port]`. The URL path, the body and the response are never stored or shown.

## Checklist for a new integration

1. Create the destination through the admin API, with `https` and no address exception
   unless the peer needs one. Set a signing secret and give the same value to the peer.
2. Send a test and confirm the peer answers 2xx and verifies the signature.
3. Create a routing rule that names the destination, with the minimum severity and the optional
   family, owner, source class, group or UTC time window. Leave `stop_default` off until the
   default channels are known to be redundant.
4. Let one real alert through and check the peer's stored body against the field table here and
   the alert's notification history against what the peer logged.
