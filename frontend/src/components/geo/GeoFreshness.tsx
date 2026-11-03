"use client";

// How old the Geography page's answer is. The widget gets this from its frame; the page has no
// frame, so this does the same judgement with the same pieces: the newest bucket's end
// (`latest_bucket_end`), the GEO_COLLECTION cadence (`useFamilyTiming`), `deriveStale` (past
// twice the cadence) and the frame's own sentences, carried by the `geo` catalog since GEO-7 so
// the route loads no `home` namespace (they are English-only while Turkish is frozen, D-41; the
// dashboard's frame keeps its `home.frame` twins). The map stays drawn either way; this only
// adds the line that says how old it is. No such time (NOT_COLLECTED, or a backend that does
// not send it) is no claim: nothing is drawn and the top bar shows no "Data to".

import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { formatClockUtc, useReportDataAge } from "@/components/shell/AutoRefresh";
import { useMeasures } from "@/i18n/measures";
import { CadenceProvider, useFamilyTiming } from "@/widgets/cadence";
import { deriveStale } from "@/widgets/state";

import "@/components/home/tables.css";

function Line({ asOf }: { asOf: string }) {
  const t = useTranslations("geo.freshness");
  const measures = useMeasures();
  const timing = useFamilyTiming("GEO_COLLECTION");
  // The clock is the browser's: the server render has none, and drawing it there would not match.
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => setNow(Date.now()), [asOf]);
  if (now === null) return null;

  const verdict = deriveStale(asOf, timing.cadenceSeconds, now);
  const time = () => <time dateTime={asOf}>{formatClockUtc(asOf)}</time>;
  if (verdict.ageSeconds === null) {
    return (
      <p className="tw-note warn" role="status">
        {t("staleUnreadable")}
      </p>
    );
  }
  if (verdict.stale) {
    return (
      <p className="tw-note warn" role="status">
        {t.rich("stale", { time, age: measures.duration(verdict.ageSeconds) })}
      </p>
    );
  }
  const age = measures.age(verdict.ageSeconds);
  return (
    <p className="tw-note">
      {age === "—" ? t.rich("dataTo", { time }) : t.rich("dataToAge", { time, age })}
    </p>
  );
}

export function GeoFreshness({ asOf }: { asOf: string | null | undefined }) {
  const at = asOf ?? null;
  useReportDataAge(at);
  if (at === null) return null;
  return (
    <CadenceProvider>
      <Line asOf={at} />
    </CadenceProvider>
  );
}
