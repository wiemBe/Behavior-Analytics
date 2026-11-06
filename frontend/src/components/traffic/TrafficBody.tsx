// What `/behavior/traffic` shows under the controls, for one answered request.
//
// The state decides the first thing on the screen. INSUFFICIENT_DATA and NOT_COLLECTED rank
// nothing and say why. PARTIAL and TRUNCATED rank what was measured and say, above the table,
// what is missing. The unattributed share is stated whenever it is known, because a row total is
// then a lower bound.

import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";

import { Pagination } from "@/components/Pagination";
import { UtcTime } from "@/components/ui/UtcTime";
import type { IpsTrafficItem, TrafficPage, WafTrafficItem } from "@/lib/api";
import { formatCount, formatShare, type FormatLocale } from "@/lib/format";
import { useTrafficReason } from "@/lib/trafficReasons";
import { ALL_ROWS, paginationParams, trafficHref, unattributedShare, type TrafficQuery } from "@/lib/traffic";

import { TrafficTable } from "./TrafficTable";

import "./traffic.css";

type Page = TrafficPage<WafTrafficItem> | TrafficPage<IpsTrafficItem>;

function Span({ data }: { data: Page }) {
  const t = useTranslations("traffic.span");
  const locale: FormatLocale = useLocale() === "tr" ? "tr" : "en";
  const width = formatCount(Math.round((data.bucket_seconds ?? 0) / 60), locale);
  const times = {
    start: () => <UtcTime value={data.range_start} />,
    end: () => <UtcTime value={data.range_end} />,
  };
  return (
    <div className="traffic-span">
      <p>
        {data.window === "5m"
          ? t.rich("newest", { width, ...times })
          : t.rich("range", times)}{" "}
        {t("basis", { width })}{" "}
        <span className="num">
          {t("measured", {
            measured: formatCount(data.buckets_measured, locale),
            expected: formatCount(data.buckets_expected, locale),
          })}
        </span>
      </p>
    </div>
  );
}

function Share({ data, view }: { data: Page; view: "waf" | "ips" }) {
  const t = useTranslations("traffic.share");
  const locale: FormatLocale = useLocale() === "tr" ? "tr" : "en";
  const noun = view === "waf" ? t("nounWaf") : t("nounIps");
  const share = unattributedShare(data.unattributed_events, data.source_total_events);
  if (share === null || data.unattributed_events === null) {
    return <p className="traffic-share muted">{t("unknown", { noun })}</p>;
  }
  return (
    <p className="traffic-share">
      {t("known", {
        unattributed: formatCount(data.unattributed_events, locale),
        total: formatCount(data.source_total_events, locale),
        share: formatShare(share, locale),
        noun,
      })}
    </p>
  );
}

export function TrafficBody({
  view,
  query,
  data,
  sourceId,
}: {
  view: "waf" | "ips";
  query: TrafficQuery;
  data: Page;
  sourceId: string;
}) {
  const t = useTranslations("traffic");
  const locale: FormatLocale = useLocale() === "tr" ? "tr" : "en";
  const nounPlural = view === "waf" ? t("share.nounWaf") + "s" : t("share.nounIps") + "s";

  const sentence = useTrafficReason(data.reason_code);
  const reason = sentence ? t("banner.reason", { sentence }) : null;

  if (data.state === "NOT_COLLECTED") {
    return (
      <div className="notice" role="status">
        <p>
          <strong>{view === "waf" ? t("notCollected.titleWaf") : t("notCollected.titleIps")}</strong>
        </p>
        <p>{view === "waf" ? t("notCollected.textWaf") : t("notCollected.textIps")}</p>
        <p>
          <Link href={view === "waf" ? "/admin/waf-sources" : "/admin/ips-sources"}>
            {view === "waf" ? t("notCollected.linkWaf") : t("notCollected.linkIps")}
          </Link>
          {data.reason_code && (
            <>
              {" "}
              <span className="muted">{reason}</span>
            </>
          )}
        </p>
      </div>
    );
  }

  if (data.state === "INSUFFICIENT_DATA") {
    return (
      <div className="notice" role="status">
        <p>
          <strong>{t("insufficient.title")}</strong>
        </p>
        <p>{t("insufficient.text")}</p>
        {data.reason_code && (
          <p className="muted">{reason}</p>
        )}
      </div>
    );
  }

  const bannerReason = data.reason_code ? (
    <> <span className="muted">{reason}</span></>
  ) : null;

  const body =
    data.items.length > 0 ? (
      <div className="traffic-results">
        <p className="traffic-summary">
          {t(data.total === 1 ? (view === "waf" ? "summary.wafOne" : "summary.ipsOne") : `summary.${view}`, {
            total: formatCount(data.total, locale),
            sort: t(`sortNames.${query.sort}`),
            order: t(`orderNames.${query.order}`),
          })}
        </p>
        {query.limit === ALL_ROWS && data.total > ALL_ROWS && (
          <p className="traffic-summary">
            {t("cut", {
              shown: formatCount(ALL_ROWS, locale),
              total: formatCount(data.total, locale),
            })}
          </p>
        )}
        <TrafficTable view={view} query={query} data={data} sourceId={sourceId} />
        {query.limit !== ALL_ROWS && (
          <Pagination
            total={data.total}
            limit={query.limit}
            offset={query.offset}
            basePath="/behavior/traffic"
            params={paginationParams(query)}
          />
        )}
      </div>
    ) : data.total > 0 ? (
      <div className="notice" role="status">
        <p>
          <strong>{t("pastEnd.title")}</strong>
        </p>
        <p>
          {t("pastEnd.text", {
            total: formatCount(data.total, locale),
            noun: nounPlural,
            offset: formatCount(data.offset, locale),
          })}
        </p>
        <p>
          <Link href={trafficHref(query, { offset: 0 })}>{t("pastEnd.link")}</Link>
        </p>
      </div>
    ) : (
      <div className="notice" role="status">
        <p>
          <strong>{view === "waf" ? t("empty.titleWaf") : t("empty.titleIps")}</strong>
        </p>
        <p>{view === "waf" ? t("empty.textWaf") : t("empty.textIps")}</p>
      </div>
    );

  return (
    <div className="stack-6">
      <div className="traffic-facts">
        <Span data={data} />
        {data.state === "PARTIAL" && (
          <div className="banner warn" role="status">
            <p>
              <strong>{t("banner.partialTitle")}</strong>{" "}
              {data.buckets_measured < data.buckets_expected
                ? t("banner.partialMissingText", {
                    measured: formatCount(data.buckets_measured, locale),
                    expected: formatCount(data.buckets_expected, locale),
                  })
                : t("banner.partialIncompleteText")}
              {bannerReason}
            </p>
          </div>
        )}
        {data.state === "TRUNCATED" && (
          <div className="banner warn" role="status">
            <p>
              <strong>{t("banner.truncatedTitle")}</strong> {t("banner.truncatedText")}
              {bannerReason}
            </p>
          </div>
        )}
        <Share data={data} view={view} />
      </div>
      {body}
    </div>
  );
}
