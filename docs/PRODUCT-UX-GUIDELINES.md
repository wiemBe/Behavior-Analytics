# Product UX Guidelines

Normative UX and semantic contracts for the QRadar behavioral anomaly and
investigation platform's web interface.

This document describes what is implemented today. It is the reference for
anyone changing the frontend, exposing UI-facing API data, writing tests, or
reviewing a UI change — including automated agents working in this repository.
Where it uses **must** / **must not**, the rule is an established product
invariant with regression coverage behind it; **should** is strong design
guidance; **may** marks a permitted alternative.

Companion documents:
[Behavioral Analytics Architecture](BEHAVIORAL-ANALYTICS-ARCHITECTURE.md) for
the engine, lifecycle and explanation model;
[Phase A design](PHASE-A-SOURCE-VOLUME-ANOMALY.md) for the source-volume
detector; UI redesign plan for the charcoal system, the
widget Dashboard and the rulings of 2026-10-14 (UR.9) behind §3, §8 and §11.

---

## 1. What this product is

This is **not** a generic QRadar dashboard, and it must not drift into one. A
dashboard reports the state of a system. This product detects that a log
source's behavior *changed*, and then explains *what* changed.

Its purpose is to:

- detect behavioral changes in log sources against a learned seasonal baseline;
- compare observed volume against expected volume;
- surface volume spikes, volume drops, and `NO_EVENTS` silence;
- represent incident lifecycle accurately;
- provide Ariel contributor evidence answering **"what changed?"**;
- support investigation without overstating certainty or claiming causation.

Everything else the interface carries — offenses, rules, coverage, inventory —
is a supporting capability. Supporting routes must not be given the visual
weight, the hierarchy, or the development attention of the behavioral ones.

The Dashboard at `/` is the one cross-cutting surface (decided on 2026-10-14). It is a home over behavior, fleet, offenses and alerts, not a behavioral
route: `/behavior` keeps the behavioral triage job and its pinned tests. The
weight rule holds on the Dashboard too — behavioral widgets take the larger
area, and each supporting capability appears at a lower weight (on the default
template, offenses hold one KPI card and one table). The Dashboard must not
drift into a generic metrics screen: a widget that does not help an analyst
decide what to look at does not belong on it.

A feature that does not help an analyst answer *"did this source change, and
what changed?"* does not belong on the behavioral routes.

---

## 2. Product UX principles

Each principle below is tied to observable behavior, because a principle that
cannot be violated in code cannot be reviewed.

**Investigation before decoration.** The first screen of any behavioral route
must answer the question the route exists for. A page that opens with equally
weighted counters answers nothing: the overview carries four counters, not ten,
and the source page opens with its identity and four metrics rather than a
technical collection table.

A row of KPI cards is permitted **only on the Dashboard**, and only when every
card in it earns its place: a different structure from its neighbours, a
breakdown derived from real fields, a drill-down link, its own honest states,
and no vanity delta. The four Dashboard KPI widgets (`KpiTotalEps`, `KpiLogSources`,
`KpiOffenses`, `KpiAnomalies`) meet this; identical
number tiles remain prohibited everywhere, and `/behavior` and the incident
page keep their pinned four-counter and comparison-block contracts (§3, §8).

**Semantic correctness before visual convenience.** When a layout is easier to
build by blurring two states that mean different things, the layout loses. A
missing measurement must not be drawn as a zero because a gap is awkward to
render.

**Progressive disclosure.** Secondary and technical material is present,
reachable, and collapsed. Nothing is deleted to simplify a page; it is deferred.
If an analyst needs the raw AQL, it is one interaction away, and it is never in
the way of the answer.

**Evidence honesty.** The interface states what it knows, how it knows it, and
what it did not check. Absence of evidence is rendered as absence, never as a
clean result. This is the single most important rule in this document: four of
the six evidence states produce a page with no contributors, which looks
identical to "we looked and nothing stood out."

**Deterministic language.** The same input produces the same sentence. Summary
text is generated from observed values and known state, not composed freely.

**No unsupported causation claims.** The engine reports what changed, never why.
See §7.

**Clear separation of overview, triage, investigation, and technical detail.**
Each route has one job (§3). Work that belongs to another route must not be
duplicated into it because it was convenient.

**Reusable components before route-specific duplication.** A presentation
problem is fixed once, in the shared component or the shared stylesheet, not
patched per call site. A rule keyed to one page's markup will be wrong on the
next page that renders the same thing.

**Responsive containment rather than clipping.** Layout defects are fixed
structurally. Hiding overflow is not a fix; it is the defect plus concealment
(§9).

---

## 3. Information architecture

### `/` — the Dashboard

Answers *"what needs me now, across the platform?"*.

- A widget dashboard: the analyst's saved layout (addressable as
  `/?dashboard=<id>`), or the dashboard marked default, or the unsaved SOC
  Overview template when there is none. Dashboards are persisted per owner;
  the template is shown unsaved and says so.
- Cross-cutting (decided on 2026-10-14): behavior, fleet, offenses and alerts in one
  view. Behavioral widgets get the larger area; supporting capabilities appear
  at a lower weight (§1).
- Widgets are registered, catalog-driven and configurable within enumerated
  options only (§8). Every widget renders its own loading, empty, no-data,
  not-collected, stale, error and forbidden state; one failed read leaves the
  others standing.
- Every widget drills down to the route that owns its data, with the filter
  pre-applied where the target supports it.

This route **must not** become the triage page (`/anomalies` and `/behavior`
keep that job), a second source inventory, or a wall of vanity numbers: a
figure is shown only where a real field backs it, a delta only where a baseline
exists (deviation from expected), and a health share reads *"n of m judged
normal"* — never a percentage that counts unjudged sources as healthy. A
comparison against a previous window that no API provides is not invented.

### `/behavior` — behavioral overview

Answers *"what needs me now?"*.

- Carries a small, fixed set of decision-relevant KPI cards.
- Carries a worklist of sources requiring attention, recently closed incidents,
  and the fleet's health distribution.
- Links onward into source investigation.

This route **must not** carry the complete source inventory. A landing page
listing every monitored source buries the handful of rows that require action
beneath the majority that do not, and inverts the page's purpose.

The count of sources with no adequate baseline is decision-relevant and keeps a
primary card. It **must not** be presented as healthy: a source that is not
being judged is an observability gap, and a fleet reporting "0 anomalies" over
unbaselined sources is reporting the absence of detection as the absence of
problems.

### `/behavior/sources` — source inventory

Answers *"which source do I want?"*, for an analyst who is looking deliberately.

- Searchable, sortable, filterable, server-paginated inventory.
- Health- and lifecycle-oriented discovery.
- Links to source detail.

This route **may** scroll its table locally. It **must not** produce page-level
horizontal overflow (§9).

Collection internals belong on the source detail page, not in the inventory row.

### `/behavior/sources/[id]` — source behavior

Answers six questions in a fixed order. The order is the deliverable and is
covered by page-composition tests:

1. **Compact source identity/header** — who this source is, and the lifecycle or
   collection status needed to read the rest. It must not restate the metrics
   below it.
2. **Exactly four primary metrics** — Observed EPS, Expected EPS, Deviation,
   Last event. No fifth primary KPI card may be added. Baseline internals,
   sample counts and collection statistics are not primary metrics.
3. **The observed-versus-expected timeline** — the dominant visual element of
   the page.
4. **Active and recent anomalies** — directly beneath the chart.
5. **Baseline quality** — is the expectation trustworthy?
6. **Collection health** — did we actually observe this window?
7. **Technical details** — raw buckets, baseline history, metadata.

Sections 5–7 are secondary and use progressive disclosure. Collapsing them
**must not** hide a critical `UNAVAILABLE` or `FAILED` condition: the disclosure
summary carries the state even when the body is closed.

### `/behavior/waf` and `/behavior/waf/sources/[id]` — WAF behavior

Answers *"which WAF source or endpoint changed?"*.

- The source list carries profile verdict, endpoint coverage and active
  endpoints; the source page carries its endpoints and their verdicts.
- Endpoint state `null` means *no detector has evaluated it* — rendered as
  such, never as `NORMAL`.
- Lower-bound measurements are marked with their own words (`≥`), apart from
  exact ones; `INSUFFICIENT_DATA` keeps its neutral tone.

### `/anomalies` — triage

Answers *"which incident do I open first?"*.

- Rapid comparison across incidents: state, severity, detector, source, timing.
- Filters, chips and pagination.
- Links into the investigation view.

This route **must not** become a dense technical evidence dump. Version stamps,
provenance and confidence internals belong behind a disclosure or on the
investigation page.

### `/anomalies/[id]` — investigation

The flagship view. Answers *"what changed during the anomalous interval?"*.

Established hierarchy:

- **Deterministic incident summary**, prominent and first.
- **Exactly four primary metrics.** No fifth primary card.
- **Timeline**, before the raw technical detail.
- **Evidence investigation** — the contributor headline and the dimension
  explorer.
- **A dimension-diverse contributor headline** (§6).
- **One contributor table on arrival.** The page must not restore the
  multi-table initial render it replaced.
- **Technical and AQL material collapsed by default**, behind the established
  tab and disclosure presentation.

### Supporting routes

Each answers one question and none competes with the behavioral routes for
weight (§1). Only routes that exist are listed; a navigation entry is a promise
that something is on the other side of it.

- **`/fleet`** — *"how healthy is each class of source?"* Window means per
  class; a figure with no complete bucket behind it is null, never 0.
- **`/fleet/sources`** — *"which source belongs to which class, and who has
  reviewed it?"* Classification and review actions live here.
- **`/fleet/catchall`** — *"which unconfigured devices send to the catch-all
  sources, and how much?"* A bucket with no stored row is not measured:
  shaded, never a zero.
- **`/offenses` and `/offenses/[id]`** — *"which offense needs a SOC analyst,
  and what is this offense?"* `magnitude`, `severity`, `credibility` and
  `relevance` are nullable and render as em dashes when null.
- **`/alerts` and `/alerts/[id]`** — *"which alerts are open, and what
  happened on this one?"*
- **`/rules` and `/rules/[id]`** — *"which rule is unhealthy, and what is this
  rule doing?"* Sections that are null say so with a reason, never as an empty
  list.
- **`/rules/statistics`** — *"what does the rule set cost?"* Lower-bound match
  rates say `≥`; truncated, failed and missing windows are counted apart.
- **`/coverage`** — *"where is detection coverage absent or degraded?"*
  `coverage_score` and `confidence` are nullable; provenance is explicit versus
  inferred.
- **`/log-sources` and `/log-sources/[id]`** — *"which sources exist, and what
  is this one's metadata?"* Both show what QRadar reports and what the
  SOC owns; neither draws a health score, because none is computed.
- **`/searches`, `/searches/new` and `/searches/[id]`** — *"which stored
  searches exist, how are they authored, and how did they run?"* The frontend
  never supplies AQL of its own to execute; a run starts a stored, validated,
  versioned query.
- **`/admin`** — the administration hub; not in the navigation.
- **`/admin/search-cadence`, `/admin/classification-rules`,
  `/admin/outage-policy`, `/admin/waf-sources`, `/admin/ips-sources`** (each
  with its detail route where one exists) — the administrative surfaces for
  collection cadence, classification, outage policy, and WAF and IPS field
  mapping.
- **`/config-changes`** — a route whose data is not collected; it says so
  rather than drawing an empty table. Not in the navigation.

---

## 4. Measurement and bucket semantics

Three states that a naive interface collapses into "0". They mean different
things and **must** be rendered differently.

### COMPLETE zero

- The bucket was collected successfully and in full.
- The measured value is genuinely zero.
- **May** be rendered as `0` or `0 EPS`.
- Represents **observed silence** — a real finding, and the basis of a
  `NO_EVENTS` anomaly.
- **Must not** be conflated with absent data.

### MISSING

- No reliable observation exists for the interval.
- **Must not** be rendered as `0`.
- **Must not** be interpreted or styled as healthy or normal.
- Charts **must** show a gap or an explicit missing state.

### PARTIAL

- The observation is incomplete.
- **Must not** support a claim of exact zero, exact volume, exact drop, or
  exact normality.
- **Must** be presented explicitly as partial or incomplete.
- **May** be excluded from definitive chart values and summarized separately.

### Rules that follow

- **Null is not measured zero.** A value that was never measured renders as an
  em dash; a value measured as zero renders as `0`. This applies to every figure
  on every behavioral route, including contributor counts, deviation ratios and
  durations.
- **The chart must not invent continuity.** `PARTIAL` and `MISSING` buckets are
  plotted as null, and the line must not connect across them. Drawing a line
  across an uncollected interval turns a collector outage into an apparent
  source outage.
- **Critical information must not exist only in chart geometry or color.** The
  chart is a canvas and is opaque to assistive technology. Anything the chart
  alone would carry must also be available as text (§10).
- **Collection health counts intervals, not rows.** An interval with no stored
  bucket is uncollected. Counting only stored buckets marked incomplete
  produces statements that are individually true and jointly misleading — "115
  of 115 fully observed" beside a chart drawn mostly as gaps.

---

## 5. Lifecycle semantics

The active anomaly states are exactly:

- `CANDIDATE`
- `OPEN`
- `RECOVERING`

Normative rules:

- Active state **must** be derived from the explicit lifecycle state.
- `resolved_at IS NULL` **must not** be used as the definition of active. It is
  lifecycle metadata, not the source of truth for current state.
- `RESOLVED` is **not** active.
- A `NORMAL` row with no resolved timestamp is **not** an active incident.
- An `OPEN`, `CANDIDATE` or `RECOVERING` row **must** remain active even if the
  payload carries a non-null `resolved_at`. Inconsistent data is interpreted
  through the explicit state, in both directions.
- `INSUFFICIENT_DATA` **must not** be presented as healthy, normal, successful,
  or green. It means no detector has judged the source.
- `SUPPRESSED` is neither a finding nor an all-clear and should stay neutral.
- UI copy **must not** invent recovery, resolution, or health from missing
  metadata. A still-running incident has no end time; it must not be given one,
  and its duration is unmeasured rather than `0s`.

---

## 6. Evidence semantics and contributor presentation

The six evidence states and their engine-side meanings are defined in
[the architecture document](BEHAVIORAL-ANALYTICS-ARCHITECTURE.md). What follows
is what the interface is permitted to claim for each.

### COMPLETE / AVAILABLE

- Contributor data **may** be presented normally.
- Counts and comparisons **must** still match the actual API contract. Complete
  evidence does not license a figure the backend did not return.

### TRUNCATED

- The returned contributor rows **may** be shown: they are a real observation.
- The UI **must** indicate clearly that the result is truncated or
  prefix-limited.
- Exact total cardinality **must not** be claimed.
- A complete set of **new** contributors **must not** be claimed.
- A complete set of **disappeared** contributors **must not** be claimed. These
  counts render as indeterminate rather than as the backend's storage default
  of `0`.
- Truncated evidence **must not** silently look complete. The truncation warning
  is stated once, globally, naming the capped dimensions.

### UNAVAILABLE

- **Must** be explicit, and rendered as its own section rather than omitted. An
  absent dimension reads to an analyst as one that *was* examined and came back
  clean.
- **Must not** look like an empty successful result.
- **Must not** be converted to zero.
- **Must not** be presented as healthy.
- It is a property of the source's DSM output, not an error.

### FAILED

- **Must** be explicit.
- **Must** be distinguishable from `UNAVAILABLE` — one is a characteristic of
  the data, the other a transient collection error.
- **Must** be distinguishable from a legitimate empty `COMPLETE` result.
- Wording **must** be understandable without inspecting a raw payload. The
  backend's sanitized reason is rendered as text, never as markup.

### Contributor presentation

- The headline is limited to **three** contributors.
- **Dimension diversity must be preserved.** When eligible contributors exist in
  more than one dimension, the headline must not consist of the three largest
  rows from a single dimension. Selection is ordered by dimension priority and
  takes at most one contributor per dimension, so a noisy source IP list cannot
  crowd out the event name that actually explains the change.
- What `TRUNCATED` evidence permits is bounded by the rules above, in the
  headline as much as in the table.
- Contributor labels **should** remain meaningful when values are long: prefer
  the resolved label over the raw identifier, fall back to the raw value when
  there is none, and wrap rather than widen the row.
- Technical payloads and query provenance belong behind progressive disclosure.

---

## 7. Summary and content rules

Incident summaries are generated, not written. They **must** be:

- deterministic — the same fixture produces the same sentence;
- descriptive rather than causal;
- derived from observed values and known lifecycle and evidence state;
- explicit about uncertainty and about evidence that is unavailable.

### Prohibited language

Summary and status copy **must not** use, or imply:

- "caused by"
- "root cause"
- "attacker"
- "compromise"
- "malicious"
- "because of"

unless the conclusion is supported by a separate trusted product signal that has
been deliberately added to the product contract. No such signal exists today.
Anthropomorphic or speculative explanation is prohibited on the same grounds.

### Preferred forms

| Instead of | Write |
|---|---|
| "traffic dropped because the firewall failed" | "Volume was below the expected range" |
| "the source stopped sending" | "No events were observed in the complete bucket" |
| an empty contributor panel | "Contributor evidence is unavailable" |
| a capped list shown as the whole list | "The returned evidence is truncated" |

Permitted hedged forms, consistent with the architecture document: "largest
observed contributor", "largest volume change", "behavior is consistent with",
"evidence suggests".

---

## Language

The interface is offered in English and Turkish; English is the default. The analyst chooses the
language under Account settings, and the server stores the choice on the user account so it follows
the analyst to other browsers. The URL never carries a locale.

Phase 1 translates interface copy: navigation, headings, controls, labels, table headers, notices
and designed loading, empty, error and honesty states. Sentences written by the backend, including
anomaly explanations, rule-health reasons, alert text and evaluation notes, remain English until
the backend exposes message codes. Frontend code must never translate those sentences by matching
their text.

The following technical terms remain English in Turkish: offense, log source, EPS, WAF, IPS, AQL,
baseline, MCP, LDAP, QRadar, DSM, QID, ATT&CK, CIDR, HTTP and UTC, along with product-object names
that match QRadar's screens. A Turkish suffix attached to an English term takes an apostrophe, for
example `offense'lar` and `log source'un`.

Turkish numbers use a dot for digit groups and a comma for decimals: `1.204` and `31,8`. The percent
sign precedes the number without a space: `%31,8`, `≥ %12,5` and `<%0,1`. English retains `1,204`,
`31.8 %` (with U+00A0), `≥ 12.5 %` and `<0.1 %`. The missing mark is `—` in both languages. Absolute
and compact timestamps are identical and explicitly UTC in both languages; only surrounding words
and relative ages are translated.

Turkish writes a count of a total as `M / N <noun>` with U+00A0 around the slash and before the
noun: `4.596 / 4.608 aralık`, `Değerlendirilen 4 kaynak: 3 normal`, `Kırpılmış: ilk 96 / 237`. It
never attaches a suffix to a number (`3'ü`, `4 kaynaktan 3 tanesi`). A number and its unit are
never separated by an ordinary space: `24 sa`, `5 dk`, `2 sa 55 dk`, `7 gün`, `968,70 EPS` and
`olay / 5 dk` use U+00A0 (English keeps its ordinary space). A Turkish sentence never starts with a
lowercase letter or with a placeholder that holds a lowercase noun: the message opens with a fixed
word (`Okunamadı: {what}.`, `Rolünüz bunu okuyamıyor: {what} (izin: {permission}).`).
`src/i18n/turkishText.contract.test.ts` pins all three rules over the Turkish catalog.

Turkish copy uses the formal register (`siz`) and short button imperatives such as `Kaydet` and
`Tekrar dene`. It uses no exclamation marks and no word meaning “live” or “real-time” (`canlı`,
`gerçek zamanlı`, `anlık`), because collection runs on a cadence. As in English, an error says what
failed and what the analyst can do.

Recurring interface terms use this glossary. A route task adds a missing word here before using it.

| English | Turkish |
|---|---|
| anomaly | anomali |
| source | kaynak |
| detector | algılayıcı |
| verdict | karar |
| status | durum |
| insufficient data | yetersiz veri |
| partial | kısmi |
| truncated | kırpılmış |
| not collected | toplanmadı |
| stale | güncel değil |
| forbidden | izin yok |
| expected range | beklenen aralık |
| bucket | zaman dilimi |
| cadence | sıklık |
| collection | toplama |
| search | arama |
| rule | kural |
| alert | uyarı |
| acknowledge | alındı olarak işaretle |
| investigation | inceleme |
| evidence | kanıt |
| contributor | katkı sağlayan |
| dashboard | gösterge paneli |
| widget | bileşen |
| settings | ayarlar |
| sign in | oturum aç |
| sign out | oturumu kapat |
| snapshot | kesit |
| severity | önem derecesi |
| lifecycle | yaşam döngüsü |
| template | şablon |
| customize | özelleştir |
| manage | yönet |
| expand | büyüt |
| discard | vazgeç |
| property watch | özellik izleme |
| scheduled search | zamanlanmış arama |
| breach | ihlal |
| shadow mode | gölge modu |
| lower bound | alt sınır |
| maintenance | bakım |
| capacity | kapasite |
| match | eşleşme |
| hour, day, second (units) | sa, gün, sn |
| fleet | filo |
| window (the time span of a view) | pencere |
| incident (an open anomaly of a class) | vaka |
| sender (catch-all) | gönderen |
| queue | kuyruk |
| dismiss | yoksay |
| override (an operator's class for one source) | geçersiz kılma |
| search family | arama ailesi |
| silence grace | sessizlik toleransı |
| coverage | kapsam |
| technique, tactic (ATT&CK) | teknik, taktik |
| entity (an IPS destination port, destination or signature) | varlık |
| signature (IPS) | imza |
| source-level (an IPS source's whole traffic) | kaynak düzeyi |
| observed, expected | gözlenen, beklenen |
| deviation | sapma |
| materiality floor | önemlilik eşiği |
| block ratio | engelleme oranı |
| destination port | hedef port |
| request | istek |
| endpoint | endpoint |
| profile | profil |
| mapping | eşleme |
| validation | doğrulama |
| virtual host | virtual host |
| admission | kabul |
| ratio | oran |
| history backfill | geçmiş veri okuma |
| scope (of a history backfill row: a console or one log source) | hedef |
| console (QRadar) | console (stays English, like log source) |
| schedule | zamanlama |
| execution, run (of a stored search) | çalıştırma |
| trigger (of a run: scheduled, manual) | tetikleyici (zamanlanmış, elle) |
| guardrails (a search's timeout, window and row caps) | koruma sınırları |
| settle (the margin before a bucket is read) | yerleşme payı |
| role | rol (a role is shown as "Label (code)"; the code stays) |
| account | hesap |
| directory (LDAP) | dizin |
| outage | kesinti |
| permission | izin (the key stays a code) |
| pattern | desen |
| built-in, custom (rule) | yerleşik, özel |
| property (QRadar) | özellik |
| instance | instance (kept English) |
| precedence, order of decision | karar sırası |

---

## 8. Component contracts

Each reusable component owns an invariant. Fix presentation problems in the
component or the shared stylesheet; do not re-solve them per route.

### `AppNav`

Grouped navigation. The active destination carries `aria-current="page"` and
exactly one link carries it. Inactive destinations carry no `aria-current`
attribute at all. A detail route marks its owning section, never that section's
path prefix. Link names must be accessible and unambiguous: no two destinations
may share an accessible name. The shell holds no heading element.

Navigation is **text only** (decided on 2026-10-14): no icons, no glyphs. An entry
exists only where a page exists — a destination with nothing behind it spends
the analyst's attention and delivers nothing. The Dashboard sits alone at the
top; the Behavior group leads the labelled groups. The active item is shown by
a raised surface and a neutral left rule, never by a status or accent hue;
`aria-current` stays the semantic carrier.

### `PageHeader`

Owns the document's single `h1` and names the page. Pages compose sections
beneath it starting at `h2`. Child content **must not** introduce a second
page-level `h1`. The description is one line of orientation: a fact (the
window, the data age), never a restatement of the title and never a promise the
product does not deliver. No tagline and no version line — no version endpoint
exists, so a version number would be invented. The `meta` row holds badges and
qualifiers and renders nothing when it is empty.

### `StatCard`

A primary, decision-relevant metric. **Must not** be used to turn every
available field into a KPI — the limits in §3 are the contract. Its content must
shrink or wrap rather than widen the page, and its optional note is where a
counter carries its own caveat, next to the number rather than in a paragraph
further down.

The Dashboard's KPI row is not built from `StatCard`: its cards are the `kpi`
variant of `WidgetFrame` (below), each with its own reads, states, breakdown
and drill-down (§2). `StatCard` keeps the behavioral pages' counters.

### `Tabs`

Real ARIA tab semantics: a labelled tab list, exactly one tab with
`aria-selected="true"` and the rest explicitly `false`, and valid
tab-to-panel relationships in both directions. Only the active panel is
rendered, so a deferred panel's content stays out of the document. Repeated
instances **must not** produce duplicate IDs; identifiers are generated per
instance.

### `Disclosure`

Native `<details>` / `<summary>`. Keyboard operable, screen-reader announced,
and functional before hydration.

**Do not** replace working native semantics with redundant ARIA state. Browsers
already expose `<summary>` as a button with an expanded state derived from the
`open` attribute; a hand-maintained `role="button"` and `aria-expanded` would
duplicate that and can drift out of sync. If a test environment cannot see the
native mapping, assert the element's `open` state and real visibility instead of
changing the component.

Secondary technical content **should** begin collapsed.

### `CodePanel`

AQL and raw technical content, with a labelled copy control. Collapsed unless
the technical content is part of the user's immediate decision. The code is
rendered as text and never as markup.

### `TableScroll`

A table's local scroll container.

- Local table scrolling is allowed. Page-level horizontal scrolling is not.
- `.table-scroll` **must** remain a positioned element. It is the containing
  block for screen-reader-only and other absolutely positioned table-local
  content; without it, an `.sr-only` element inside a table wider than its
  container resolves against the initial containing block, is laid out past the
  right edge of the viewport, and widens the document even though the table
  itself scrolls correctly.
- **Do not** remove that positioning contract without equivalent regression
  protection.
- The region is labelled and keyboard-reachable, so a table that scrolls can be
  scrolled without a pointer.

### `EvidenceBanner`

Communicates evidence completeness and its limitations, once and globally.
Meaning **must not** rely on color alone: the status is named in text, and the
reason a count is withheld is explained rather than shown as zero.

### `DimensionExplorer`

Displays one active contributor dimension and one table at a time; switching
dimensions must not add a second table. Every dimension is listed, including
those never collected, and each carries its status. Evidence-state limitations
travel with the dimension: an `UNAVAILABLE` dimension shows a status panel
rather than an empty table, and a `TRUNCATED` one keeps its rows while
withholding its counts.

### `LifecycleTimeline`

Exposes lifecycle events as meaningful text in an ordered list, so the sequence
is conveyed structurally. Timestamps use real `time` elements. Timeline position
and color **must not** be the only carriers of meaning, and decorative markers
are hidden from assistive technology. An unmeasured transition value renders as
an em dash, never as zero.

### `TechnicalDetails`

Secondary and raw implementation detail — thresholds, versions, provenance.
**Must not** dominate the initial investigation hierarchy, and starts collapsed.

### `IncidentMetrics`

The incident page's four primary metrics — observed, expected, deviation,
duration — rendered as **one comparison block**, not four identical cards. The
observed value carries the most weight; expected and deviation sit beside it at
a lower weight; duration is smallest. Every figure carries its unit, and
detector confidence is a labelled footer of the block. A null renders as an em
dash: an unmeasured value is not a zero, and a still-running incident has no
duration.

### `LoadingState` / `ErrorState`

Every data route tree has a `loading.tsx` and an `error.tsx` built from these
(`components/ui/RouteState.tsx`). Both are plain text — no spinner, no
skeleton. `LoadingState` is a polite live region naming what is loading.
`ErrorState` is an alert that says the page could not be displayed, that
nothing was changed, and what to try; it **must not** render the exception
text, which can carry a stack trace or an upstream URL. Next's error digest may
be shown as a reference. Its `what` argument is one translated noun phrase in
the nominative so English and Turkish can compose the surrounding sentence.

### `TopBar`, `TopSearch`, `AutoRefresh`, `AlertBell`, `ConnectionStatus`

The shell's top bar (`components/shell/`). Its elements read existing list and
health endpoints only; **no QRadar search is issued from the bar**, and search
results are links, never actions.

- `TopSearch` is a combobox (arrow keys, `aria-activedescendant`, Escape,
  Ctrl+K) over lists that already exist: sources, rules, offenses, and active
  anomalies (client-filtered over one bounded read, its group labelled with the
  cap). A group the role may not read (`403`) is omitted from the results and
  named once in a footer line — never drawn as "no results".
- `AutoRefresh` offers Off, 1 min, 5 min. It shows two distinct times:
  *refreshed* (when the browser last re-read) and *data to* (how far the data
  reaches, supplied by the page, never inferred). It pauses while the tab is
  hidden and while a dialog is open. **No copy anywhere says "live" or
  "real-time"**: collection runs on a cadence, and a page refresh is not a
  fresher measurement.
- `AlertBell` shows the open-alert count and links to `/alerts`. A failed read
  shows no badge and an "unavailable" name — **never `0`**. Its accessible name
  carries the count or says it is unavailable.
- `ConnectionStatus` shows the worst of three named signals (backend, QRadar
  instance, collection). `UNKNOWN` is neutral, never green; a signal the role
  may not read is omitted, not shown as bad; a signal with no data source is
  not invented.

### `WidgetFrame`

The one frame every Dashboard widget shares (`components/home/`). It owns the
title (which says what and for what window), the optional window control,
Expand, the drill-down link and the states. The link stays in the header in
**every** state, so an analyst can reach the page that owns the data even when
the widget could not be read. Its states are `loading`, `ok`, `stale`, `empty`,
`no_data`, `not_collected`, `error` and `forbidden`; the honesty qualifiers
`INSUFFICIENT_DATA`, `PARTIAL`, `TRUNCATED` and `NOT_COLLECTED` attach to data
the widget does have, as pills and reasons beside the figures. No state renders
an exception's text. `forbidden` names the permission and says nothing was
changed. Stale means the data's `asOf` is older than twice the collection
cadence; it is derived, never stored, and shown with the time it is from. A
failed refresh keeps the previous answer on screen with a note saying so.

### `useApiResource`

The one read hook the widgets share (`hooks/`). Callers with the same request
key share one in-flight request, so six widgets reading the same URL send it
once. A `403` is its own state; every other failure is an error with the HTTP
status only. Requests abort when the last caller unmounts, and the shell's
refresh tick re-reads them all.

### `DashboardView` and the widget registry

`/` renders a layout — a saved dashboard's or the template's — through the
registry (`widgets/registry.ts`). The registry is the single list of placeable
widgets; each entry names its data sources and required permissions, and the registry
test asserts the sources are routes the client actually calls, the permissions
exist in the backend, and no widget is `live` while its data is a gap. The
grid is 12 columns when its container is at least 900 px wide, 6 from 600 px,
1 below; only the 12-column layout is stored and the narrower ones are derived,
in reading order, without overlap. Editing is offered only at 12 columns —
below that the page shows the dashboard read-only with "Widen the window to
customize". A widget of an unknown type renders "Unsupported widget" and keeps
its place: an older client must not destroy a newer layout. Off-screen widget
bodies are deferred: the box is drawn at its grid size and the body mounts when
the box comes near the viewport, so an off-screen chart is never created and
the layout never shifts.

### `EditGrid` (customize mode)

Drag and resize live behind the Customize toggle, so a stray drag cannot move
anything in read mode. The grid library loads only when editing starts. The
title row is the drag handle. Pointer editing has a keyboard equivalent: per
widget, Move and Resize steppers change the cell geometry by one cell, with
bound-aware disabled states. A widget **must not** be removable only by
pointer.

### `Catalog`, `Configure`, `ExpandDialog`

- `Catalog` is the add-widget drawer: a real `<dialog>`, focus to Close on
  open, returned to the trigger on close. It lists only what exists, with the
  data each widget reads and the permission it needs; a `blocked` widget is
  listed disabled with its reason, and a widget the caller's role cannot read
  is disabled with the permission named. One dashboard holds at most 24
  widgets.
- `Configure` offers enumerated options only — pickers over ids and enums from
  the real APIs. No free text, and nothing that could reach the server as a
  query to execute; the dashboard API runs nothing from a layout.
- `ExpandDialog` re-renders the same widget larger in a native `<dialog>`;
  Escape closes and focus returns to the trigger.

### `RangeControl`

The per-widget window (`1h | 24h | 7d`): preset buttons where they fit, a
named select where they do not. A widget without window presets follows the
dashboard's default; the effective window is shown in the widget's title.
There is **no global time range**: the dashboard's window belongs to the
dashboard layout and rewrites nothing outside it; page-local selectors keep
their own contracts.

### `useDashboard`, `DashboardSwitcher`, `DashboardMenu`

One reducer owns a dashboard's saved layout, current layout and save state; a
failed or conflicting write changes only the save state and never discards the
analyst's work. Unsaved changes guard navigation away (a dialog with Keep and
Discard, in-page links included). A `409` revision conflict offers Reload and
Save as a copy — nothing is overwritten silently. A caller without the write
permission still sees the dashboard and may expand and configure in memory;
changes then last for the session only, and the page says so.

---

## 9. Responsive layout

### Supported verification viewports

- 1440 × 900
- 1280 × 800
- 1024 × 768

### Acceptance condition

At every supported viewport, on every verified route:

```javascript
document.documentElement.scrollWidth ===
document.documentElement.clientWidth
```

### Rules

- There **must** be no page-level horizontal overflow.
- Local table scrolling **is** acceptable, and is the intended mechanism for a
  table wider than its column. A scroll container's own
  `scrollWidth > clientWidth` is not a page-level defect and must not be
  reported as one.
- Grid and flex children generally require `min-width: 0`. Their default
  `min-width: auto` refuses to shrink below content, which is how one long
  label holds a whole row open.
- The shell's content column carries `min-width: 0`; without it the widest
  table widens the column and then the page.
- Responsive KPI layouts use auto-fit with a `minmax` track whose minimum
  cannot exceed the container — for example
  `repeat(auto-fit, minmax(min(100%, 200px), 1fr))`.
- Flexible search fields use a shrinkable track — `minmax(0, 1fr)` in grid, or a
  flex basis with `min-width: 0` — so the field yields width instead of pushing
  a trailing control off the page.
- Intrinsic controls (selects, buttons, date inputs) **should not** be
  stretched; they take their content width and wrap when the row runs out.
- Long labels, source names and badges **must** wrap or shrink safely. Long text
  children need `min-width: 0` and controlled wrapping.
- Severity badges remain content-sized and non-clipping: a badge refuses to
  shrink below its text, and a long sibling label wraps instead of squeezing it.
- Charts **must** remain contained by their parent layout at every supported
  width.
- The Dashboard grid derives its narrow layouts from the stored 12-column one
  (§8); at 1024×768 it reflows to 6 columns with no page overflow, and editing
  is replaced by the honest narrow fallback ("Widen the window to customize").
  A widget box taller than its body scrolls inside the box; it is never
  clipped. Below 12 columns the read-only rows grow with their content and
  widget bodies are not deferred.
- The grid positions cells with unitless custom properties on `.dg-item` — the
  one accepted inline style in the widget system: layout numbers, never a
  colour or a length.
- A widget title row that cannot hold the title, the qualifier pill and its
  controls on one line moves only the pill into the body, decided by measured
  fit rather than a fixed width, so it cannot flip back and forth on resize.

### Prohibited

- `overflow-x: hidden` on `html`, `body`, the app shell, or a page container to
  conceal a layout defect. It hides the symptom and leaves the cause, and it
  makes the acceptance condition above pass for the wrong reason.
- Absolute positioning as a general solution for label and badge layout. Use a
  flex wrapper with a natural `gap`, `min-width: 0` on the label side, and
  `flex: 0 0 auto` plus `white-space: nowrap` on the badge side.
- Severity-specific hardcoded widths, or any CSS keyed to a severity name.
- Fixed desktop widths that force viewport overflow.
- Clipping text to make a measurement pass.

---

## 10. Accessibility

The rules below are contracts with regression coverage. This repository has
**not** completed a formal WCAG audit, and no conformance level is claimed.

- Exactly one page-level `h1` per route, owned by `PageHeader`; sections start
  at `h2`.
- Sections, regions and interactive controls carry accessible names.
- Active navigation uses `aria-current="page"` (§8).
- Tabs expose valid, resolving tab-and-panel relationships.
- Disclosures use native semantics (§8).
- Timestamps are UTC and always carry the zone (`formatDateTime`,
  `formatDateTimeCompact`). A time without a zone is ambiguous to an analyst
  comparing it with QRadar or another system. A relative age ("12 min ago")
  **may** follow the absolute time in muted text, never replace it, and times
  in tables and widgets are real `time` elements with a `datetime` value.
- Non-inline interactive controls are at least 24 × 24 px (the WCAG 2.5.8
  minimum, held by the `--target-min` token and pinned by
  `controlTargetContract.test.ts`). Inline links inside text keep the inline
  exception.
- An ARIA relationship (`aria-controls`) is published only while the element it
  names is mounted: a closed search result list, a lazily unmounted tab panel
  and a collapsed arrange panel expose none. A reference that resolves to
  nothing reads as present in the markup and absent to a screen reader.
- Status and severity meaning is available in text. **Color must never be the
  sole carrier of meaning** — this covers severity (`LOW`, `MEDIUM`, `HIGH`,
  `CRITICAL`), lifecycle state including `INSUFFICIENT_DATA`, and evidence and
  dimension states including `UNAVAILABLE`, `FAILED` and truncation. No two
  states in a set may reduce to the same words, and only a genuinely good
  outcome may take a positive tone.
- A severity never takes the positive tone: `LOW` and `INFO` are `info`, not
  `ok` (`sevTone`). A low-severity anomaly is still an anomaly.
- Tables use real column-header semantics, carry an accessible name or caption,
  and their sorting and pagination controls have names identifying the column
  and the action or state. Every production table is named — a source-wide
  regression (`TableNames.test.ts`) identifies any that is not.
- Charts and timelines carry textual alternatives. The volume chart is exposed
  as a named image with a text summary of what it shows; an empty window leaves
  no named but meaningless image behind.
- Repeated interactive components generate unique IDs; ARIA references must
  resolve to elements that exist. A dangling `aria-controls` reads as present in
  the markup and absent to a screen reader.
- Screen-reader-only content must remain inside an appropriately positioned
  container (§8, `TableScroll`).
- Collapsed technical content **must not** be presented as visible initial
  evidence.

---

## 11. Tokens and colour

The palette is charcoal, defined once in `frontend/src/app/tokens.css`. The
product is **dark only** (decided on 2026-10-14): `color-scheme: dark`, no light theme,
no `prefers-color-scheme` rule. Charts read their colours once, at creation
(`lib/chartPalette.ts`, `lib/home/chartPalette.ts`), so a second theme is not
one token block away, and no theme switch exists.

- **Status colour is reserved for status.** Red, amber and green never decorate
  a border, a heading or a category. A categorical series uses
  `--chart-cat-1…5`, with `--chart-other` for the aggregate "Other" slice; none
  of them is a status hue, and `contrast.test.ts` asserts exactly that. A
  series that *is* a status — an anomaly marker, a gap band, a failed run —
  uses the status hues.
- **No chart relies on hue alone.** A series is named in a legend with its
  value, in the tooltip, and for a part-to-whole chart in a list beside it. A
  donut holds at most five slices: four named categories plus "Other". The
  graphic itself is hidden from assistive technology, which gets the same
  figures as text.
- **Severity colour in charts.** Pills keep one `crit` tone for `CRITICAL` and
  `HIGH` and carry the word (§10); there is no orange token and none is added.
  Where a chart needs one mark per severity it uses `--sev-critical`,
  `--sev-high` (a darker red), `--sev-medium`, `--sev-low` and `--sev-info` —
  **charts only** — with a legend and tooltips carrying the words. CSS keyed to
  a severity name stays prohibited (§9).
- **A magnitude on a map is a sequential scale, not a status scale.** A
  choropleth uses the one-hue ramp `--map-0…--map-4` with a numeric legend; a
  Low-green, Medium-amber, High-red ramp would make "events from this country"
  read as "this country is dangerous". (The geography widget that will consume
  the ramp is not built yet; the tokens and the backend aggregate exist.)
- **Solid fills with alpha, no gradients.** Chart fills are solid alpha tints
  (`--chart-band`, the status `-bg` tints); no gradient fill, no glow, no
  glassmorphism. The only gradients in the stylesheets are the table-scroll
  covers and edge shadow — a functional scrolling affordance, not decoration.
  There is one shadow token, `--shadow`; elevation is carried by layer and
  border, not by stacked shadows.
- **Cyan is the one data accent.** `--info` (cyan) is the observed series and
  the `LOW` tone; links use `--link`; the primary button is neutral light
  (`--primary` with `--on-accent` text); the focus ring is neutral
  (`--focus`). Blue is not the identity. The word "info" and the `info` class
  names are unchanged.
- **No colour, spacing or type literal outside `tokens.css`.** The colour half
  is enforced by `frontend/src/app/colourLiterals.test.ts`, which sweeps every
  stylesheet under `src` for hex, `rgb()`/`hsl()` and named colours (only
  `transparent`, `currentColor` and `inherit` are allowed), and by the same
  check inside `contrast.test.ts`. Spacing and type literals are held by the
  same rule in review. A palette change is a token change and nothing else.
- **Contrast is tested** (`lib/contrast.test.ts`): `--text`, `--text-2` and
  `--neutral-text` meet AA 4.5:1 on every surface; the status text tints meet
  it on their tinted fills; the chart categories are at least 3:1 on
  `--surface`.

### The contrast exception of 2026-10-14, and its exact bounds

The decision of 2026-10-14 keeps two palette values although they are
below the WCAG thresholds: `--text-3 = #73757B` is below AA 4.5:1 on every
surface, and `--border-strong = #35363A` is below the 3:1 non-text minimum for
an input boundary. The exception covers **exactly these two values and nothing
else**. It is not permission for a third low-contrast value, and no new colour
may be added below a threshold it should meet. `contrast.test.ts` pins both
values instead of asserting them and asserts that no `--text-disabled` token
exists — disabled text uses `--text-3`.

### Typography

Inter, self-hosted (`app/fonts/`, `app/fonts.css`, SIL OFL): the product is
on-premises, so no request leaves for a font at build time or at runtime. The
redesign added no new type size; the existing scale is used by role (page title,
section heading, widget title, table header, body, metric), no size below 12 px
is defined, and numbers use tabular figures (`.num`).

---

## 12. Testing expectations

- Test user-observable behavior and established semantics, not implementation
  details.
- Prefer accessible role and name queries over element selectors and class
  names.
- Use realistic fixtures, including hostile ones: long source names, every
  severity, and all three bucket completeness states.
- Ensure important regression tests are **non-vacuous**. Verify a new guard by
  breaking the behavior it protects and confirming the intended test fails, then
  restore the implementation immediately.
- Avoid large snapshots as the primary assertion strategy.
- Do not reproduce production algorithms in test code; a test that recomputes
  the thing it checks proves only that the code was copied correctly.
- Avoid assertions tied to harmless wording or class changes. A test that fails
  when a sentence is reworded pushes copy in the wrong direction — assert the
  invariant, not the phrasing.
- Use CSS contract tests **only** where a CSS property is itself the regression
  boundary, such as the `TableScroll` positioning contract.
- Page tests cover hierarchy and composition — section order, metric counts,
  cross-component semantics.
- Component and helper tests cover reusable semantics, and are the right layer
  for anything more than one route renders. Do not add a page test for an
  invariant a shared component test already proves completely.
- Evidence and lifecycle edge cases must be explicit, not incidental.
- Null versus zero, and the status distinctions in §4 and §10, require
  dedicated regression coverage.
- A widget registered in the catalog is pinned by the registry test: its
  `sources` are routes the client actually calls, its `requires` are
  permissions the backend defines, no widget is `live` while its data is a gap,
  and a template holds only `live` widgets. The frame's state union is tested
  at both densities, and each catalog widget is tested through its loading,
  error and forbidden states.
- CSS contracts that are themselves the regression boundary — the colour
  literal sweep, the contrast thresholds, the control target size, the table
  names — read the stylesheets or the DOM and name the file that breaks
  (§10, §11).

External browser and viewport verification **complements** the jsdom suite and
must not be confused with it. jsdom does not lay out, so it can guard the
structure that lets CSS work but can never prove the absence of overflow; that
proof comes from measuring a real browser at the viewports in §9. Neither
substitutes for the other.

---

## 13. Change checklist

For use during code review of any UI change.

- [ ] Is the route's information hierarchy preserved (§3)?
- [ ] Are primary metrics still limited to four, with no fifth KPI?
- [ ] Are null, measured zero, `MISSING` and `PARTIAL` still distinct (§4)?
- [ ] Is active state derived from the explicit lifecycle state, never from
      `resolved_at` (§5)?
- [ ] Are evidence limitations — truncation, unavailable, failed — visible and
      distinguishable (§6)?
- [ ] Is contributor dimension diversity preserved (§6)?
- [ ] Is generated content deterministic and free of causal claims (§7)?
- [ ] Is technical detail progressively disclosed and collapsed by default?
- [ ] Is every meaning available without color (§10)?
- [ ] Are ARIA relationships valid, unique, and native semantics left intact?
- [ ] Is there page-level horizontal overflow at 1440, 1280 or 1024 — and was
      any overflow fixed structurally rather than hidden (§9)?
- [ ] Is every new colour, spacing or type value a token, with no literal
      added outside `tokens.css` (§11)?
- [ ] Does a chart use the categorical or severity tokens, never a status hue,
      and does more than hue carry its meaning (§11)?
- [ ] On the Dashboard, does every KPI card keep a breakdown, a drill-down and
      its honest states, with no vanity delta (§2, §8)?
- [ ] Is a new widget registered with real sources and permissions, tested in
      every state, and `blocked` while its data does not exist (§8)?
- [ ] Do times carry UTC and their zone, with any relative age secondary
      (§10)? Does copy avoid "live" and "real-time"?
- [ ] Do non-inline interactive controls keep the 24 px minimum target, and
      does an added dialog or drawer return focus to its trigger (§10)?
- [ ] Do the targeted tests and the full frontend gates pass?
- [ ] Was the change kept inside one coherent workstream, without unrelated
      fixes, dependency upgrades or scope expansion?
