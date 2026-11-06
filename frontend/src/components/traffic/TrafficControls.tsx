// The controls of `/behavior/traffic`: plain links, so every choice is in the URL and the screen
// can be shared. The chosen option carries `aria-current`. A new window, view, source or page
// size goes back to the first page.

import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";

import {
  ALL_ROWS,
  TRAFFIC_PAGE_SIZES,
  TRAFFIC_VIEWS,
  TRAFFIC_WINDOWS,
  trafficHref,
  type TrafficQuery,
} from "@/lib/traffic";
import { trafficCsvUrl, type TrafficPage } from "@/lib/api";
import { formatCount, type FormatLocale } from "@/lib/format";

export interface TrafficSourceChoice {
  id: string;
  name: string;
}

function Group({
  title,
  label,
  children,
}: {
  title: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <nav className="range-selector traffic-group" aria-label={label}>
      <span className="muted">{title}</span>
      {children}
    </nav>
  );
}

function Option({
  href,
  active,
  children,
}: {
  href: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className={`range-option${active ? " range-option-active" : ""}`}
      aria-current={active ? "page" : undefined}
    >
      {children}
    </Link>
  );
}

export type ExportBasis =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "data"; page: Pick<TrafficPage<unknown>, "state" | "total"> };

/** The export: a link when there is a set to export, else a span that says why there is none. */
function ExportCsv({
  query,
  sourceId,
  basis,
}: {
  query: TrafficQuery;
  sourceId: string;
  basis: ExportBasis;
}) {
  const t = useTranslations("traffic.export");
  const locale: FormatLocale = useLocale() === "tr" ? "tr" : "en";
  let why: string | null = null;
  if (!sourceId || basis.kind === "error") why = t("disabledError");
  else if (basis.kind === "loading") why = t("disabledLoading");
  else if (basis.page.state === "NOT_COLLECTED") why = t("disabledNotCollected");
  else if (basis.page.state === "INSUFFICIENT_DATA") why = t("disabledInsufficient");
  else if (basis.page.total === 0) why = t("disabledEmpty");
  if (why !== null || basis.kind !== "data") {
    return (
      <span className="traffic-export traffic-export-off" aria-disabled="true">
        <span>{t("label")}</span> <span className="muted">— {why}</span>
      </span>
    );
  }
  const noun = query.view === "waf" ? t("nounWaf") : t("nounIps");
  return (
    <span className="traffic-export">
      <a
        href={trafficCsvUrl(query.view, sourceId, {
          window: query.window,
          sort: query.sort,
          order: query.order,
        })}
        download
      >
        {t("link", {
          total: formatCount(basis.page.total, locale),
          noun,
          window: t(`windows.${query.window}`),
        })}
      </a>{" "}
      <span className="muted">{t("note")}</span>
    </span>
  );
}

export function TrafficControls({
  query,
  sourceId,
  sources,
  basis = { kind: "error" },
}: {
  basis?: ExportBasis;
  query: TrafficQuery;
  /** The source the page is showing. */
  sourceId: string;
  sources: TrafficSourceChoice[];
}) {
  const t = useTranslations("traffic");
  return (
    <div className="traffic-controls">
      <Group title={t("controls.view")} label={t("views.label")}>
        {TRAFFIC_VIEWS.map((view) => (
          <Option
            key={view}
            // A source of the other view is meaningless here, and so are its sorts.
            href={trafficHref(query, {
              view,
              source: "",
              sort: "rate",
              order: "desc",
              offset: 0,
            })}
            active={view === query.view}
          >
            {t(`views.${view}`)}
          </Option>
        ))}
      </Group>
      {sources.length > 1 && (
        <Group title={t("controls.source")} label={t("controls.sourceLabel")}>
          {sources.map((source) => (
            <Option
              key={source.id}
              href={trafficHref(query, { source: source.id, offset: 0 })}
              active={source.id === sourceId}
            >
              {source.name}
            </Option>
          ))}
        </Group>
      )}
      <Group title={t("controls.window")} label={t("controls.windowLabel")}>
        {TRAFFIC_WINDOWS.map((window) => (
          <Option
            key={window}
            href={trafficHref(query, { window, offset: 0 })}
            active={window === query.window}
          >
            {t(`windows.${window}`)}
          </Option>
        ))}
      </Group>
      <Group title={t("controls.pageSize")} label={t("controls.pageSizeLabel")}>
        {TRAFFIC_PAGE_SIZES.map((limit) => (
          <Option
            key={limit}
            href={trafficHref(query, { limit, offset: 0 })}
            active={limit === query.limit}
          >
            {limit}
          </Option>
        ))}
        <Option
          href={trafficHref(query, { limit: ALL_ROWS, offset: 0 })}
          active={query.limit === ALL_ROWS}
        >
          {t("controls.all")}
        </Option>
      </Group>
      <ExportCsv query={query} sourceId={sourceId} basis={basis} />
    </div>
  );
}
