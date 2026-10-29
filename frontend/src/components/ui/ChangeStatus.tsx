// The outcome line under a control that changes a stored setting.
//
// A pending change is announced politely; a refused or failed one is an alert.
// The wording always says whether the setting changed, so the outcome does not
// depend on the tone.

import type { ReactNode } from "react";
import { useTranslations } from "next-intl";

import "./controls.css";

export type ChangeOutcome =
  | { kind: "idle" }
  | { kind: "pending" }
  | { kind: "ok"; text: ReactNode }
  | { kind: "notice"; text: ReactNode }
  | { kind: "forbidden"; text: string }
  | { kind: "error"; text: string };

/** A permission name (`admin:search_cadence`) is set in code and never broken. */
function withPermissionCode(text: string): ReactNode[] {
  return text.split(/(\b[a-z]+:[a-z_]+\b)/).map((part, i) =>
    i % 2 === 1 ? (
      <code key={i} className="perm">
        {part}
      </code>
    ) : (
      part
    ),
  );
}

export function ChangeStatus({ outcome }: { outcome: ChangeOutcome }) {
  const t = useTranslations("common");
  if (outcome.kind === "idle") return null;
  if (outcome.kind === "pending") {
    return (
      <p className="change-status" role="status">
        {t("saving")}
      </p>
    );
  }
  if (outcome.kind === "ok") {
    return (
      <p className="change-status ok" role="status">
        {typeof outcome.text === "string" ? withPermissionCode(outcome.text) : outcome.text}
      </p>
    );
  }
  if (outcome.kind === "notice") {
    return (
      <p className="change-status neutral" role="status">
        {typeof outcome.text === "string" ? withPermissionCode(outcome.text) : outcome.text}
      </p>
    );
  }
  return (
    <p className="change-status crit" role="alert">
      {withPermissionCode(outcome.text)}
    </p>
  );
}
