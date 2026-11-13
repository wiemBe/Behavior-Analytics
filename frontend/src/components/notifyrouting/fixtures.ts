// Test data for the notification routing page. Shaped like `app/schemas/notification_destination.py`
// and `notification_rule.py`; no value is a real host, address or credential.

import type { NotificationDestination, NotificationRule } from "@/lib/api";

export function destination(over: Partial<NotificationDestination> = {}): NotificationDestination {
  return {
    id: "d-1",
    name: "SOAR",
    enabled: true,
    url_display: "https://soar.example",
    allow_http: false,
    allow_private: false,
    secret_set: true,
    header_names: ["X-Team"],
    last_test_at: "2026-11-13T09:00:00Z",
    last_test_status: 204,
    last_test_class: "ok",
    created_by: "admin",
    created_at: "2026-11-01T08:00:00Z",
    updated_by: "admin",
    updated_at: "2026-11-13T09:00:00Z",
    ...over,
  };
}

export function rule(over: Partial<NotificationRule> = {}): NotificationRule {
  return {
    id: "r-1",
    name: "Critical to SOAR",
    enabled: true,
    priority: 10,
    min_severity: "HIGH",
    families: ["waf"],
    owners: null,
    source_classes: null,
    group_ids: null,
    window_start_minute: null,
    window_end_minute: null,
    destinations: [{ id: "d-1", name: "SOAR" }],
    stop_default: false,
    created_by: "admin",
    created_at: "2026-11-01T08:00:00Z",
    updated_by: "admin",
    updated_at: "2026-11-13T09:00:00Z",
    ...over,
  };
}
