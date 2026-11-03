// Test data for the alert e-mail page. Shaped like `app/schemas/email.py`; no value is a real
// address, host or credential.

import type {
  EmailPlaceholders,
  EmailPreview,
  EmailRecipient,
  EmailRecipientList,
  EmailSettings,
  EmailTemplate,
  EmailTemplateList,
  EmailTemplateSummary,
} from "@/lib/api";

export const ALERT_TYPES = [
  { key: "log_source", label: "Log source" },
  { key: "waf_endpoint", label: "WAF endpoint" },
  { key: "ips_entity", label: "IPS entity" },
  { key: "custom_rule", label: "Custom rule" },
];

export function settings(over: Partial<EmailSettings> = {}): EmailSettings {
  return {
    configured: true,
    host: "smtp.relay.example",
    port: 587,
    security: "starttls",
    security_source: "SMTP_SECURITY",
    from_address: "alerts@qradar.example",
    from_name: "QRadar Behavior Analytics",
    username_set: true,
    ca_file_set: false,
    site_name: "lab-console",
    test_rate_limit: "5/minute",
    ...over,
  };
}

export function summary(
  key: string,
  label: string,
  over: Partial<EmailTemplateSummary> = {},
): EmailTemplateSummary {
  return {
    key,
    alert_type: key === "default" ? null : key,
    label,
    customised: false,
    enabled: false,
    updated_at: null,
    updated_by: null,
    ...over,
  };
}

/** Nothing customised: every row sends the shipped template. */
export function plainList(): EmailTemplateList {
  return {
    templates: [
      summary("default", "Default"),
      ...ALERT_TYPES.map((type) => summary(type.key, type.label)),
    ],
    alert_types: ALERT_TYPES,
  };
}

/** A stored default and a stored WAF endpoint template. */
export function customisedList(): EmailTemplateList {
  return {
    templates: [
      summary("default", "Default", {
        customised: true,
        enabled: true,
        updated_at: "2026-11-02T09:15:00Z",
        updated_by: "a.rivera",
      }),
      summary("log_source", "Log source"),
      summary("waf_endpoint", "WAF endpoint", {
        customised: true,
        enabled: true,
        updated_at: "2026-11-03T08:40:00Z",
        updated_by: "j.okafor",
      }),
      summary("ips_entity", "IPS entity"),
      summary("custom_rule", "Custom rule", {
        customised: true,
        enabled: false,
        updated_at: "2026-11-01T17:05:00Z",
        updated_by: "a.rivera",
      }),
    ],
    alert_types: ALERT_TYPES,
  };
}

export const SHIPPED_HTML =
  '<table width="600"><tbody><tr><td>{{ alert.title }}</td></tr></tbody></table>';

export function template(key: string, over: Partial<EmailTemplate> = {}): EmailTemplate {
  const label = key === "default" ? "Default" : (ALERT_TYPES.find((t) => t.key === key)?.label ?? key);
  return {
    key,
    alert_type: key === "default" ? null : key,
    label,
    customised: false,
    enabled: false,
    effective_source: "shipped",
    subject: "[{{ alert.severity }}] {{ alert.title }} ({{ alert.transition }})",
    html_body: SHIPPED_HTML,
    text_body: "",
    updated_at: null,
    updated_by: null,
    ...over,
  };
}

export function placeholders(): EmailPlaceholders {
  return {
    syntax: "{{ name }}",
    placeholders: [
      { name: "alert.title", description: "The alert's title." },
      { name: "alert.severity", description: "The severity: CRITICAL, HIGH, MEDIUM, LOW or INFO." },
      { name: "alert.url", description: "The alert's page on this site." },
      { name: "site.name", description: "This site's name (SITE_NAME)." },
    ],
  };
}

export function preview(over: Partial<EmailPreview> = {}): EmailPreview {
  return {
    subject: "[HIGH] Alert: this is a preview (OPENED)",
    html: "<table><tbody><tr><td>Alert: this is a preview</td></tr></tbody></table>",
    text: "Alert: this is a preview\nOpen the alert: https://qradar.example/alerts/1",
    sanitized_html_body: SHIPPED_HTML,
    sample_alert_type: "default",
    ...over,
  };
}

export function recipient(over: Partial<EmailRecipient> = {}): EmailRecipient {
  return {
    id: "r-1",
    address: "soc@example.org",
    min_severity: "LOW",
    alert_types: null,
    enabled: true,
    created_at: "2026-11-01T10:00:00Z",
    updated_at: "2026-11-02T10:00:00Z",
    updated_by: "a.rivera",
    ...over,
  };
}

export function recipientList(over: Partial<EmailRecipientList> = {}): EmailRecipientList {
  return {
    recipients: [
      recipient(),
      recipient({
        id: "r-2",
        address: "oncall@example.org",
        min_severity: "HIGH",
        alert_types: ["waf_endpoint", "ips_entity"],
        updated_by: "j.okafor",
      }),
      recipient({ id: "r-3", address: "audit@example.org", enabled: false, min_severity: "CRITICAL" }),
    ],
    from_env: ["noc@example.org"],
    ...over,
  };
}
