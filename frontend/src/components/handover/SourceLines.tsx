// One line per frozen source that was not read cleanly. A FAILED or FORBIDDEN source is named
// and explained; its rows are missing, and no count is drawn in their place.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { SOURCE_ROUTES } from "@/components/queue/SourceStrip";
import type { QueueKind } from "@/lib/api";
import type { SourceLine } from "@/lib/handover";

const ACTED_ROUTES: Record<string, string> = { triage: "/anomalies", alert: "/alerts" };

export function SourceLines({ lines, preview = false }: { lines: SourceLine[]; preview?: boolean }) {
  const t = useTranslations("handover");
  const q = useTranslations("queue.sources");
  // A preview speaks of the viewer and of the record that would be written, never of one written.
  const ns = preview ? "preview" : "source";
  if (lines.length === 0) return null;

  return (
    <section className="ho-sources" aria-label={t("record.sourcesHeading")}>
      <h2 className="ho-sources-title">{t("record.sourcesHeading")}</h2>
      <ul className="ho-source-list">
        {lines.map((line) => {
          const s = line.source;
          const name =
            line.scope === "open"
              ? q(`kind.${s.kind as QueueKind}`)
              : s.kind === "alert"
                ? t("source.kind.alert")
                : t("source.kind.triage");
          const href = line.scope === "open" ? SOURCE_ROUTES[s.kind as QueueKind] : ACTED_ROUTES[s.kind];
          const tone = s.state === "FAILED" ? "crit" : "warn";
          let text: string;
          switch (s.state) {
            case "FAILED":
              text = t(`${ns}.failed`, { name });
              break;
            case "FORBIDDEN":
              text = s.permission
                ? t(`${ns}.forbidden`, { name, permission: s.permission })
                : t(`${ns}.forbiddenUnknown`, { name });
              break;
            case "NOT_COLLECTED":
              text = t(`${ns}.notCollected`, { name });
              break;
            case "STALE":
              text = t(`${ns}.stale`, { name });
              break;
            default:
              text = t(`${ns}.partial`, { name });
          }
          return (
            <li key={`${line.scope}:${s.kind}`} className={`ho-source ho-source-${tone}`}>
              <span>{text}</span>
              {s.reason_code && <span className="muted"> {t("source.reason", { code: s.reason_code })}</span>}
              {href && (
                <>
                  {" "}
                  <Link href={href}>{name}</Link>
                </>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
