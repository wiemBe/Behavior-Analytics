// Words for a directory connection test. The reason text stays as the backend
// sent it. The status codes do not.

export type OutcomeTone = "ok" | "crit" | "neutral";

export interface OutcomeWord {
  label: string;
  tone: OutcomeTone;
  detail: string | null;
}

export function tlsOutcome(status: string, reason: string | null): OutcomeWord {
  if (status === "ok") return { label: "OK", tone: "ok", detail: blank(reason) };
  if (status === "failed") return { label: "Failed", tone: "crit", detail: blank(reason) };
  return { label: words(status), tone: "neutral", detail: blank(reason) };
}

export function bindOutcome(status: string, reason: string | null): OutcomeWord {
  if (status === "ok") return { label: "OK", tone: "ok", detail: blank(reason) };
  if (status === "not_attempted") return { label: "Not attempted", tone: "neutral", detail: blank(reason) };
  if (status === "invalid_credentials") return { label: "Refused", tone: "crit", detail: blank(reason) };
  return { label: words(status), tone: "neutral", detail: blank(reason) };
}

export function trustWords(trust: string): string {
  if (trust === "system") return "System trust store";
  if (trust === "ca_pem") return "Uploaded CA certificate";
  return trust;
}

/** One sentence for the test. A failed TLS check never reads as a bind. */
export function probeHeadline(tlsStatus: string, bindStatus: string, username: string): string {
  if (tlsStatus !== "ok") return "Connection failed at TLS.";
  if (bindStatus === "ok") {
    const name = username.trim();
    return name ? `Connected and bound as ${name}.` : "Connected and bound.";
  }
  return "TLS succeeded. The bind did not.";
}

function blank(value: string | null): string | null {
  if (value == null || value.trim() === "") return null;
  return value;
}

function words(status: string): string {
  const text = status.replaceAll("_", " ").trim();
  if (text === "") return "Unknown";
  return text.charAt(0).toUpperCase() + text.slice(1);
}
