// Scope tabs and filters of the queue. Every control is a link to the same view with one thing
// changed, so the URL is the state, Back and Forward walk the history, and a copied link shows the
// same view. An active choice is told apart by weight and border, not by a hue.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { useEnumLabels } from "@/i18n/enumLabels";
import {
  QUEUE_FAMILIES,
  QUEUE_KINDS,
  QUEUE_SCOPES,
  QUEUE_SEVERITIES,
  isFiltered,
  queueHref,
  withFirstPage,
  type QueueQuery,
} from "@/lib/queue";
import type { QueueFamily, QueueKind, QueueSeverity } from "@/lib/api";

function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

export function QueueFilters({ query, scopeTotal }: { query: QueueQuery; scopeTotal: string | null }) {
  const t = useTranslations("queue");
  const { severityPill } = useEnumLabels();
  const filtered = isFiltered(query);

  return (
    <div className="queue-filters">
      <nav className="queue-scopes" aria-label={t("scope.label")}>
        {QUEUE_SCOPES.map((scope) => {
          const on = query.scope === scope;
          return (
            <Link
              key={scope}
              href={queueHref(query, withFirstPage({ scope }))}
              className={`queue-scope${on ? " queue-scope-on" : ""}`}
              aria-current={on ? "page" : undefined}
            >
              {t(`scope.${scope}`)}
              {on && scopeTotal !== null && <span className="queue-scope-count num">{scopeTotal}</span>}
            </Link>
          );
        })}
      </nav>

      <div className="queue-facets" role="group" aria-label={t("filters.label")}>
        <span className="facet-group" role="group" aria-label={t("filters.kind")}>
          <span className="queue-facet-name">{t("filters.kind")}</span>
          {QUEUE_KINDS.map((kind: QueueKind) => (
            <Link
              key={kind}
              href={queueHref(query, withFirstPage({ kinds: toggle(query.kinds, kind) }))}
              className="facet"
              aria-current={query.kinds.includes(kind) ? "true" : undefined}
            >
              {t(`sources.kind.${kind}`)}
            </Link>
          ))}
        </span>
        <span className="facet-group" role="group" aria-label={t("filters.family")}>
          <span className="queue-facet-name">{t("filters.family")}</span>
          {QUEUE_FAMILIES.map((family: QueueFamily) => (
            <Link
              key={family}
              href={queueHref(query, withFirstPage({ families: toggle(query.families, family) }))}
              className="facet"
              aria-current={query.families.includes(family) ? "true" : undefined}
            >
              {t(`filters.family_${family}`)}
            </Link>
          ))}
        </span>
        <span className="facet-group" role="group" aria-label={t("filters.severity")}>
          <span className="queue-facet-name">{t("filters.severity")}</span>
          {QUEUE_SEVERITIES.map((severity: QueueSeverity) => (
            <Link
              key={severity}
              href={queueHref(query, withFirstPage({ severities: toggle(query.severities, severity) }))}
              className="facet"
              aria-current={query.severities.includes(severity) ? "true" : undefined}
            >
              {severityPill(severity)}
            </Link>
          ))}
        </span>
        <span className="facet-group">
          <Link
            href={queueHref(query, withFirstPage({ newOnly: !query.newOnly }))}
            className="facet"
            aria-current={query.newOnly ? "true" : undefined}
          >
            {t("filters.newOnly")}
          </Link>
          {filtered && (
            <Link
              href={queueHref(query, withFirstPage({ kinds: [], families: [], severities: [], newOnly: false }))}
              className="filter-clear"
            >
              {t("filters.clear")}
            </Link>
          )}
        </span>
      </div>
    </div>
  );
}
