// What the platform has already stored about one IP address, host or user, in one window.

import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";

import { EntityForm } from "@/components/entities/EntityForm";
import { EntityRange } from "@/components/entities/EntityRange";
import { LaneTable } from "@/components/entities/LaneTable";
import { TimelineTable } from "@/components/entities/TimelineTable";
import { PageHeader } from "@/components/ui/PageHeader";
import { renderBackendMessage } from "@/i18n/backendMessage";
import type { Locale } from "@/i18n/locale";
import { withRouteMessages } from "@/i18n/RouteMessages";
import { asTextTranslator } from "@/i18n/textTranslator";
import { ApiError, api, type EntityTimeline } from "@/lib/api";
import {
  ENTITY_VALUE_MAX,
  KIND_REQUIRED_CODE,
  entityHref,
  entityWindow,
  parseEntityQuery,
} from "@/lib/entities";
import { formatCount } from "@/lib/format";
import { guideFor } from "@/lib/guideRoutes";
import { loadErrorMessage } from "@/lib/loadError";
import { refusalSentences } from "@/lib/refusal";

import "./entities.css";

const CURSOR_INVALID_CODE = "entity_timeline.input.cursor_invalid";
const REFUSAL_ID = "entity-refusal";

export async function generateMetadata() {
  const t = await getTranslations("entities");
  return { title: t("meta") };
}

async function EntitiesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const t = await getTranslations("entities");
  const say = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as Locale;
  const query = parseEntityQuery(await searchParams);
  const guide = guideFor("/entities");

  // Nothing typed yet: the page is its form.
  if (query.value === "") {
    return (
      <>
        <PageHeader guide={guide} title={t("title")} description={t("description")} />
        <div className="entities">
          <EntityForm value="" kind="ip" range={query.range} />
        </div>
      </>
    );
  }

  const typedTitle = query.kind
    ? t("titleEntity", { kind: t(`kind.${query.kind.toUpperCase() as "IP" | "HOST" | "USER"}`), value: query.value })
    : query.value;

  if (query.valueTooLong) {
    return (
      <>
        <PageHeader guide={guide} title={t("title")} description={t("description")} />
        <div className="entities">
          <div className="notice" role="alert" id={REFUSAL_ID}>
            <p>{t("form.tooLong", { max: formatCount(ENTITY_VALUE_MAX, locale) })}</p>
          </div>
          <EntityForm value={query.value.slice(0, ENTITY_VALUE_MAX)} kind={query.kind} range={query.range} describedBy={REFUSAL_ID} />
        </div>
      </>
    );
  }

  const nowMs = Date.now();
  let timeline: EntityTimeline | null = null;
  let failure: unknown = null;
  try {
    timeline = await api.entityTimeline({
      value: query.value,
      kind: query.kind || undefined,
      ...entityWindow(query.range, nowMs),
      cursor: query.cursor || undefined,
    });
  } catch (err) {
    failure = err;
  }

  if (timeline === null) {
    const refused = failure instanceof ApiError && failure.status === 422;
    const sentence = refused && failure instanceof ApiError ? refusalSentences(failure)[0] : undefined;
    const code = sentence?.code ?? null;
    const kindNeeded = code === KIND_REQUIRED_CODE;
    return (
      <>
        <PageHeader guide={guide} title={typedTitle} description={t("description")} />
        <div className="entities">
          {refused ? (
            <div className="notice" role="alert" id={REFUSAL_ID}>
              <p>
                <strong>{t("refused.title")}</strong>
              </p>
              <p>{sentence ? renderBackendMessage(sentence, locale, say) : t("refused.generic")}</p>
              {kindNeeded && <p>{t("form.chooseKind", { value: query.value })}</p>}
              {code === CURSOR_INVALID_CODE && (
                <p>
                  <Link href={entityHref({ value: query.value, kind: query.kind, range: query.range })}>
                    {t("refused.newest")}
                  </Link>
                </p>
              )}
            </div>
          ) : (
            <div className="notice" role="alert">
              <p>{loadErrorMessage(failure, t("loadNoun"), "read:anomalies", say)}</p>
            </div>
          )}
          <EntityForm
            value={query.value}
            kind={kindNeeded ? "" : query.kind}
            range={query.range}
            describedBy={refused ? REFUSAL_ID : undefined}
          />
        </div>
      </>
    );
  }

  const { entity, lanes, items } = timeline;
  const paged = timeline.truncated || query.cursor !== "";
  const pageNote =
    timeline.state === "PARTIAL" || timeline.state === "NOT_COLLECTED" || timeline.state === "TRUNCATED"
      ? t(`page.${timeline.state}`)
      : timeline.state === "OK"
        ? null
        : t("page.OTHER");
  const olderHref = timeline.next_cursor
    ? entityHref({ value: query.value, kind: query.kind, range: query.range, cursor: timeline.next_cursor })
    : null;
  const newestHref = query.cursor
    ? entityHref({ value: query.value, kind: query.kind, range: query.range })
    : null;

  return (
    <>
      <PageHeader
        guide={guide}
        title={t("titleEntity", { kind: t(`kind.${entity.kind}`), value: entity.value_norm })}
        description={t("description")}
      />
      <div className="entities">
        <EntityForm
          value={query.value}
          kind={query.kind || (entity.kind.toLowerCase() as "ip" | "host" | "user")}
          range={query.range}
        />
        <EntityRange value={query.value} kind={query.kind} active={query.range} window={timeline.window} />

        {pageNote && (
          <p className="banner warn" role="status">
            <span>
              <strong>{t(`state.${timeline.state}`)}.</strong> {pageNote}
            </span>
          </p>
        )}

        {items.length === 0 ? (
          <>
            <div className="notice" role="status">
              <p>
                <strong>{t("empty.none", { value: entity.value_norm })}</strong>
              </p>
              <p>{t("empty.caution")}</p>
            </div>
            <LaneTable lanes={lanes} paged={paged} />
          </>
        ) : (
          <>
            <LaneTable lanes={lanes} paged={paged} />
            <section aria-labelledby="entity-items-heading" className="entity-items">
              <h2 id="entity-items-heading" className="section-title">
                {t("items.heading")}
              </h2>
              <TimelineTable items={items} nowMs={nowMs} />
              <nav className="pagination" aria-label={t("paging.label")}>
                <span>{t("paging.shown", { count: items.length })}</span>
                <span className="pagination-links">
                  {newestHref && <Link href={newestHref}>{t("paging.newest")}</Link>}
                  {olderHref && <Link href={olderHref}>{t("paging.older")}</Link>}
                </span>
              </nav>
            </section>
          </>
        )}

        {timeline.dangling > 0 && (
          <p className="muted entity-dangling">{t("dangling", { count: timeline.dangling })}</p>
        )}
      </div>
    </>
  );
}

export default withRouteMessages("/entities", EntitiesPage);
