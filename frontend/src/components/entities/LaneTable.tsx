// The honesty strip of the entity timeline: one row per lane, its status in words, the count it
// measured and why it is not complete. A compact table, not a row of cards. Colour only reinforces
// the status word; "none stored" is said only where the lane proved absence.

import { useLocale, useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import { asTextTranslator } from "@/i18n/textTranslator";
import type { EntityTimelineLaneOut } from "@/lib/api";
import { formatCount, type FormatLocale } from "@/lib/format";

import { laneView } from "./laneView";

export function LaneTable({
  lanes,
  paged,
}: {
  lanes: EntityTimelineLaneOut[];
  /** The list is a page (a cursor, or a truncated answer), so a count is for that page. */
  paged: boolean;
}) {
  const t = useTranslations("entities.lanes");
  const stateT = useTranslations("entities.state");
  const say = asTextTranslator(useTranslations());
  const locale = (useLocale() === "tr" ? "tr" : "en") as FormatLocale;

  /** A reason code the catalog knows, in words; never the code itself. */
  function reason(lane: EntityTimelineLaneOut): string {
    const key = lane.reason_code ? `backend.${lane.reason_code}` : null;
    if (key && say.has?.(key)) {
      return say(key, lane.permission ? { permission: lane.permission } : {});
    }
    return t("noReason");
  }

  return (
    <section className="entity-lanes" aria-labelledby="entity-lanes-heading">
      <h2 id="entity-lanes-heading" className="section-title">
        {t("heading")}
      </h2>
      <TableScroll label={t("label")}>
        <table className="table-dense entity-lane-table" aria-label={t("label")}>
          <thead>
            <tr>
              <th scope="col">{t("lane")}</th>
              <th scope="col">{t("status")}</th>
              <th scope="col" className="num">
                {paged ? t("onPage") : t("inWindow")}
              </th>
              <th scope="col">{t("note")}</th>
            </tr>
          </thead>
          <tbody>
            {lanes.map((lane) => {
              const view = laneView(lane, paged);
              const note =
                view.note === "noneProven"
                  ? t("noneProven")
                  : view.note === "notCounted"
                    ? t("notCounted")
                    : view.note === "forbidden"
                      ? lane.permission
                        ? t("forbidden", { permission: lane.permission })
                        : t("forbiddenUnknown")
                      : view.note === "reason"
                        ? reason(lane)
                        : null;
              return (
                <tr key={lane.source}>
                  <th scope="row" className="entity-lane-name">
                    {t(`name.${lane.source}`)}
                  </th>
                  <td>
                    <span className={`pill pill-quiet${view.tone ? ` ${view.tone}` : ""}`}>
                      {stateT(view.word)}
                    </span>
                  </td>
                  <td className="num">
                    {view.count === null ? (
                      <span className="muted">
                        —<span className="sr-only"> {t("notMeasured")}</span>
                      </span>
                    ) : (
                      formatCount(view.count, locale)
                    )}
                  </td>
                  <td className="entity-lane-note">{note ?? <span className="muted">—</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableScroll>
      {paged && <p className="muted entity-paged-note">{t("pagedNote")}</p>}
    </section>
  );
}
