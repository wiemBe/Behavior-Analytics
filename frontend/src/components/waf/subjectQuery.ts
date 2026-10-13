// The subject filters of the anomaly list, parsed from the query string.
//
// `lib/anomalyQuery.ts` is Phase A's parser and allow-lists only Phase A
// detector types. These two filters are parsed here with the same rule: an
// allow-list, so a crafted value is dropped rather than forwarded upstream or
// reflected back into the page.

import type { SubjectKind } from "@/lib/api";

import { isWafType } from "./wafLabels";

export const SUBJECT_KINDS: Array<{ value: SubjectKind; label: string }> = [
  { value: "LOG_SOURCE", label: "Log sources" },
  { value: "WAF_ENDPOINT", label: "WAF endpoints" },
];

export interface SubjectQuery {
  /** "" when unset: both kinds are listed. */
  subject_kind: SubjectKind | "";
  /** A WAF detector type, or "" (Phase A types are parsed by `parseAnomalyQuery`). */
  waf_anomaly_type: string;
}

export function parseSubjectQuery(params: Record<string, string | undefined>): SubjectQuery {
  const kind = SUBJECT_KINDS.find((k) => k.value === params.subject_kind)?.value ?? "";
  const type = params.anomaly_type;
  return {
    subject_kind: kind,
    waf_anomaly_type: type && isWafType(type) ? type : "",
  };
}

export function subjectKindLabel(value: string): string {
  return SUBJECT_KINDS.find((k) => k.value === value)?.label ?? value;
}
