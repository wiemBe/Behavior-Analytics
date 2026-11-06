// What `/behavior/noise` shows under the controls, for one answered request.
//
// Order of the screen: when the list was last evaluated, a PARTIAL banner if the stored window is
// shorter than asked, the ranked table (or the empty state), the footnote that says which columns
// ignore the window selector, and the collapsed footer that counts the sources that raised nothing.

import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";

import { Pagination } from "@/components/Pagination";
import { Disclosure } from "@/components/ui/Disclosure";
import { UtcTime } from "@/components/ui/UtcTime";
import type { NoiseSourcesPage } from "@/lib/api";
import { formatCount, type FormatLocale } from "@/lib/format";
import { NOISE_PAGE_SIZE, noiseHref, noisePaginationParams, type NoiseQuery } from "@/lib/noise";

import { NoiseTable } from "./NoiseTable";

import "./noise.css";

function Facts({ data, query }: { data: NoiseSourcesPage; query: NoiseQuery }) {
  const t = useTranslations("noise.facts");
  const orderName = useTranslations("noise.orderNames");
  return (
    <div className="noise-facts">
      <p>
        {data.last_collection
          ? t.rich("collection", { time: () => <UtcTime value={data.last_collection} /> })
          : t("noCollection")}
      </p>
      {data.total > 0 && (
        <p>
          {t("summary", {
            total: data.total,
            days: query.window,
            order: orderName(query.order),
          })}
        </p>
      )}
    </div>
  );
}

function Quiet({ data, days }: { data: NoiseSourcesPage; days: number }) {
  const t = useTranslations("noise.quiet");
  if (data.zero_fired === 0) {
    return data.total > 0 ? <p className="noise-footnote">{t("none", { days })}</p> : null;
  }
  return (
    <Disclosure summary={t("summary", { count: data.zero_fired })} note={t("note", { days })}>
      <div className="noise-quiet">
        {data.zero_fired_judged > 0 && <p>{t("judged", { count: data.zero_fired_judged, days })}</p>}
        {data.zero_fired_insufficient_data > 0 && (
          <p>{t("notJudged", { count: data.zero_fired_insufficient_data })}</p>
        )}
      </div>
    </Disclosure>
  );
}

export function NoiseBody({ query, data }: { query: NoiseQuery; data: NoiseSourcesPage }) {
  const t = useTranslations("noise");
  const locale: FormatLocale = useLocale() === "tr" ? "tr" : "en";
  const days = query.window;
  const partial = data.coverage.state === "PARTIAL";

  const list =
    data.items.length > 0 ? (
      <div>
        <NoiseTable query={query} data={data} />
        {data.total > NOISE_PAGE_SIZE && (
          <Pagination
            total={data.total}
            limit={NOISE_PAGE_SIZE}
            offset={data.offset}
            basePath="/behavior/noise"
            params={noisePaginationParams(query)}
          />
        )}
        <p className="noise-footnote">{t("footnote")}</p>
      </div>
    ) : data.total > 0 ? (
      <div className="notice" role="status">
        <p>
          <strong>{t("pastEnd.title")}</strong>
        </p>
        <p>
          {t("pastEnd.text", {
            total: data.total,
            offset: formatCount(data.offset, locale),
          })}
        </p>
        <p>
          <Link href={noiseHref(query, { offset: 0 })}>{t("pastEnd.link")}</Link>
        </p>
      </div>
    ) : (
      <div className="notice" role="status">
        <p>
          <strong>{t("empty.title")}</strong>
        </p>
        <p>{data.empty_reason?.text ?? t("empty.fallback", { days })}</p>
        <p>
          {data.last_collection
            ? t.rich("empty.withCollection", {
                time: () => <UtcTime value={data.last_collection} />,
              })
            : t("empty.noCollection")}
        </p>
      </div>
    );

  return (
    <div className="stack-6">
      <Facts data={data} query={query} />
      {partial && (
        <div className="banner warn" role="status">
          <p>
            <strong>{t("partial.title")}</strong>{" "}
            {data.coverage.reason?.text ??
              t("partial.covered", {
                covered: formatCount(data.coverage.covered_days, locale),
                window: formatCount(data.coverage.window_days, locale),
              })}{" "}
            {t("partial.text")}
          </p>
        </div>
      )}
      {list}
      <Quiet data={data} days={days} />
    </div>
  );
}
