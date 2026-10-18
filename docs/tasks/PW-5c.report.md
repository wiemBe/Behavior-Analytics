STATUS: FINAL

# PW-5c report

Files changed: anomalies route, anomaly and anomaly_histogram repositories, schemas/anomaly.py
(snapshot, reviewed and fixed); property_watches route, service and schema (AQL view, values
filter); new tests/integration/test_pw_api_gaps.py; test_anomaly_histogram.py (families list).

Found wrong in the snapshot: `routes/anomalies.py` used PwAnomaly, PropertyWatch,
PwAnomalyTransition, PwScopeKind and pw_detection_detail without importing them (mypy: 5 errors;
the PW detail would have crashed). Fixed. The histogram's three expected `families` lists were
edited by the snapshot (needed: PW_VALUE is now a family); I kept them, as a constant.

Differences for the UI vs the PW-7 fixture: `detection` is a typed PwDetectionDetailOut (named
keys only, band in expected_low/high), not None; `explanation_package` is None; `pw_value.value_id`
is null once retention deleted the value; transitions are oldest first.

AQL: `GET /property-watches/{id}/aql` -> {current: GROUPED+TOTALS built by build_query over the
preview window with no Ariel call (without the provider's START/STOP suffix), current_unavailable
(reason when scope does not resolve), last_bucket: whitelisted query texts of the newest bucket}.

Gates: ruff clean; mypy 0 errors (265 files); unit 4317 passed, 10 skipped; whole integration
1386 passed, 0 failed, 0 skipped (log: /home/efe/Documents/QRadar-Dash-work/pw5c/integration.log).
Statements: PW detail constant (test compares 1 vs 21 incidents/transitions); list_values equal at
50 and 200 values and 1 and 10 watches, <= 6.
Mutations (each failed a test, then restored): detail disabled (4), value_id null (1), histogram
PW branch off (4), list_values watch filter removed (1; first run survived, test tightened),
provenance whitelist dropped (1).
Open points: the AQL "current" text lacks START/STOP and the provider's row-cap clamp, so the
LIMIT may differ from a real run for huge caps; PW anomaly alerts/engine untouched.
