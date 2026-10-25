// Words for a directory connection test. The reason text stays as the backend
// sent it. The status codes do not.

import { englishText, type TextTranslator } from "@/i18n/fallback";

export type OutcomeTone = "ok" | "crit" | "neutral";

export interface OutcomeWord {
  label: string;
  tone: OutcomeTone;
  detail: string | null;
}

export function tlsOutcome(
  status: string,
  reason: string | null,
  t: TextTranslator = englishText,
): OutcomeWord {
  if (status === "ok") return { label: t("auth.probeOk"), tone: "ok", detail: blank(reason) };
  if (status === "failed") return { label: t("auth.probeFailed"), tone: "crit", detail: blank(reason) };
  return { label: words(status, t), tone: "neutral", detail: blank(reason) };
}

export function bindOutcome(
  status: string,
  reason: string | null,
  t: TextTranslator = englishText,
): OutcomeWord {
  if (status === "ok") return { label: t("auth.probeOk"), tone: "ok", detail: blank(reason) };
  if (status === "not_attempted") return { label: t("auth.probeNotAttempted"), tone: "neutral", detail: blank(reason) };
  if (status === "invalid_credentials") return { label: t("auth.probeRefused"), tone: "crit", detail: blank(reason) };
  return { label: words(status, t), tone: "neutral", detail: blank(reason) };
}

export function trustWords(trust: string, t: TextTranslator = englishText): string {
  if (trust === "system") return t("auth.systemTrust");
  if (trust === "ca_pem") return t("auth.uploadedCa");
  return trust;
}

/** One sentence for the test. A failed TLS check never reads as a bind. */
export function probeHeadline(
  tlsStatus: string,
  bindStatus: string,
  username: string,
  t: TextTranslator = englishText,
): string {
  if (tlsStatus !== "ok") return t("auth.tlsFailed");
  if (bindStatus === "ok") {
    const name = username.trim();
    return name ? t("auth.connectedBoundAs", { name }) : t("auth.connectedBound");
  }
  return t("auth.bindFailed");
}

function blank(value: string | null): string | null {
  if (value == null || value.trim() === "") return null;
  return value;
}

function words(status: string, t: TextTranslator): string {
  const text = status.replaceAll("_", " ").trim();
  if (text === "") return t("common.unknown");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
