"use client";

import Link from "next/link";
import { useEffect, useId, useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";

import {
  customRuleDelayText,
  subjectKindInSentence,
  subjectKindLabel,
} from "@/components/customrules/authoring";
import { latencyCadence } from "@/components/cadence/CadenceLatency";
import {
  loadIpsEntities,
  loadIpsSources,
  loadWafEndpoints,
  loadWafSources,
} from "@/components/customrules/inventory";
import {
  api,
  type CustomRuleCatalog,
  type CustomRuleSubjectKind,
  type IpsEntityInventory,
  type IpsSourceBehavior,
  type SourceBehavior,
  type WafEndpointInventory,
  type WafSourceBehavior,
} from "@/lib/api";
import type { Locale } from "@/i18n/locale";
import { asTextTranslator } from "@/i18n/textTranslator";

interface SubjectValue {
  logSourceId: string;
  sourceClass: string;
  wafEndpointId: string;
  ipsEntityId: string;
  pwValueId: string;
}

export interface SubjectPickerProps {
  catalog: CustomRuleCatalog;
  kind: CustomRuleSubjectKind;
  value: SubjectValue;
  onKind: (kind: CustomRuleSubjectKind) => void;
  onChange: (value: SubjectValue) => void;
  onLabel: (label: string) => void;
}

type LoadState = "idle" | "loading" | "ready" | "error";

function contains(value: string, query: string): boolean {
  return value.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
}

function endpointLabel(endpoint: WafEndpointInventory): string {
  const host = endpoint.virtual_host ? `${endpoint.virtual_host} ` : "";
  return `${host}${endpoint.http_method} ${endpoint.normalized_endpoint}`;
}

function entityLabel(entity: IpsEntityInventory): string {
  return `${entity.dimension}: ${entity.display_name ?? entity.value_key}`;
}

export function SubjectPicker({ catalog, kind, value, onKind, onChange, onLabel }: SubjectPickerProps) {
  const t = useTranslations("customRules.picker");
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as Locale;
  const pinCadence = latencyCadence(catalog.cadences?.find((c) => c.family === "PROPERTY_WATCH"));
  const pinDelay = customRuleDelayText(pinCadence, locale, say);
  const availability = pinDelay
    ? t("availabilityKnown", { duration: pinDelay })
    : t("availabilityUnknown");
  const kindId = useId();
  const searchId = useId();
  const parentId = useId();
  const subjectId = useId();
  const [state, setState] = useState<LoadState>("idle");
  const [query, setQuery] = useState("");
  const [sources, setSources] = useState<SourceBehavior[]>([]);
  const [wafSources, setWafSources] = useState<WafSourceBehavior[]>([]);
  const [ipsSources, setIpsSources] = useState<IpsSourceBehavior[]>([]);
  const [parentSourceId, setParentSourceId] = useState("");
  const [endpoints, setEndpoints] = useState<WafEndpointInventory[]>([]);
  const [entities, setEntities] = useState<IpsEntityInventory[]>([]);
  const [watches, setWatches] = useState<Awaited<ReturnType<typeof api.propertyWatches>>>([]);
  const [watchId, setWatchId] = useState("");
  const [pinnedValues, setPinnedValues] = useState<Awaited<ReturnType<typeof api.propertyWatchValues>>["items"]>([]);

  useEffect(() => {
    let current = true;
    setState("loading");
    setQuery("");
    setEndpoints([]);
    setEntities([]);
    const load = async () => {
      try {
        if (kind === "LOG_SOURCE") {
          const rows = await api.sourceBehaviors();
          if (current) setSources(rows);
        } else if (kind === "WAF_ENDPOINT") {
          const rows = await loadWafSources();
          if (current) setWafSources(rows);
        } else if (kind === "IPS_SOURCE" || kind === "IPS_ENTITY") {
          const rows = await loadIpsSources();
          if (current) setIpsSources(rows);
        } else if (kind === "PW_VALUE") {
          const rows = await api.propertyWatches();
          if (current) setWatches(rows);
        }
        if (current) setState("ready");
      } catch {
        if (current) setState("error");
      }
    };
    void load();
    return () => {
      current = false;
    };
  }, [kind]);

  useEffect(() => {
    if (!parentSourceId || (kind !== "WAF_ENDPOINT" && kind !== "IPS_ENTITY")) return;
    let current = true;
    setState("loading");
    const load = async () => {
      try {
        if (kind === "WAF_ENDPOINT") {
          const rows = await loadWafEndpoints(parentSourceId, true);
          if (current) setEndpoints(rows);
        } else {
          const rows = await loadIpsEntities(parentSourceId);
          if (current) setEntities(rows.filter((entity) => entity.active));
        }
        if (current) setState("ready");
      } catch {
        if (current) setState("error");
      }
    };
    void load();
    return () => {
      current = false;
    };
  }, [kind, parentSourceId]);

  useEffect(() => {
    if (kind !== "PW_VALUE" || !watchId) return;
    let current = true;
    setState("loading");
    api.propertyWatchValues(watchId, { limit: 50, offset: 0 })
      .then((page) => {
        if (!current) return;
        setPinnedValues(page.items.filter((item) => item.pinned));
        setState("ready");
      })
      .catch(() => {
        if (current) setState("error");
      });
    return () => {
      current = false;
    };
  }, [kind, watchId]);

  const filteredSources = useMemo(
    () => sources.filter((source) => contains(source.name, query)),
    [query, sources],
  );
  const filteredIpsSources = useMemo(
    () => ipsSources.filter((source) => contains(source.log_source_name, query)),
    [ipsSources, query],
  );
  const filteredWatches = useMemo(
    () => watches.filter((watch) => contains(watch.name, query)),
    [query, watches],
  );

  function setLogSource(id: string, label: string) {
    onChange({ ...value, logSourceId: id });
    onLabel(label);
  }

  const loading = state === "loading";

  return (
    <div className="cr-subject-picker">
      <div className="cr-field">
        <label htmlFor={kindId}>{t("kind")}</label>
        <select
          id={kindId}
          className="select-control"
          value={kind}
          onChange={(event) => onKind(event.target.value as CustomRuleSubjectKind)}
        >
          {catalog.subject_kinds.map((subject) => (
            <option key={subject.subject_kind} value={subject.subject_kind}>
              {subjectKindLabel(subject.subject_kind, say)}
            </option>
          ))}
        </select>
      </div>

      {kind === "FLEET_CLASS" ? (
        <div className="cr-field">
          <label htmlFor={subjectId}>{t("fleetClass")}</label>
          <select
            id={subjectId}
            className="select-control"
            value={value.sourceClass}
            onChange={(event) => {
              onChange({ ...value, sourceClass: event.target.value });
              onLabel(event.target.value);
            }}
          >
            <option value="">{t("chooseFleet")}</option>
            {catalog.source_classes.map((sourceClass) => (
              <option key={sourceClass} value={sourceClass}>{sourceClass}</option>
            ))}
          </select>
        </div>
      ) : (
        <div className="cr-field">
          <label htmlFor={searchId}>
            {kind === "PW_VALUE"
              ? t("findWatch")
              : t("findSubject", { subject: subjectKindInSentence(kind, say) })}
          </label>
          <input
            id={searchId}
            className="field"
            type="search"
            value={query}
            placeholder={
              kind === "PW_VALUE"
                ? t("filterWatches")
                : t("filterSources")
            }
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      )}

      {kind === "LOG_SOURCE" && (
        <div className="cr-field">
          <label htmlFor={subjectId}>{t("logSource")}</label>
          <select
            id={subjectId}
            className="select-control"
            value={value.logSourceId}
            disabled={loading}
            onChange={(event) => {
              const source = sources.find((row) => row.log_source_id === event.target.value);
              setLogSource(event.target.value, source?.name ?? event.target.value);
            }}
          >
            <option value="">{loading ? t("loadingSources") : t("chooseLogSource")}</option>
            {filteredSources.map((source) => (
              <option key={source.log_source_id} value={source.log_source_id}>{source.name}</option>
            ))}
          </select>
        </div>
      )}

      {kind === "IPS_SOURCE" && (
        <div className="cr-field">
          <label htmlFor={subjectId}>{t("ipsSource")}</label>
          <select
            id={subjectId}
            className="select-control"
            value={value.logSourceId}
            disabled={loading}
            onChange={(event) => {
              const source = ipsSources.find((row) => row.log_source_id === event.target.value);
              setLogSource(event.target.value, source?.log_source_name ?? event.target.value);
            }}
          >
            <option value="">{loading ? t("loadingSources") : t("chooseIpsSource")}</option>
            {filteredIpsSources.map((source) => (
              <option key={source.log_source_id} value={source.log_source_id}>{source.log_source_name}</option>
            ))}
          </select>
        </div>
      )}

      {(kind === "WAF_ENDPOINT" || kind === "IPS_ENTITY") && (
        <>
          <div className="cr-field">
            <label htmlFor={parentId}>{kind === "WAF_ENDPOINT" ? t("wafSource") : t("ipsSource")}</label>
            <select
              id={parentId}
              className="select-control"
              value={parentSourceId}
              disabled={loading && !parentSourceId}
              onChange={(event) => {
                setParentSourceId(event.target.value);
                setEndpoints([]);
                setEntities([]);
                onChange({ ...value, wafEndpointId: "", ipsEntityId: "" });
                onLabel("");
              }}
            >
              <option value="">{t("chooseSourceFirst")}</option>
              {(kind === "WAF_ENDPOINT" ? wafSources : filteredIpsSources).map((source) => (
                <option key={source.log_source_id} value={source.log_source_id}>
                  {source.log_source_name}
                </option>
              ))}
            </select>
          </div>
          <div className="cr-field">
            <label htmlFor={subjectId}>{kind === "WAF_ENDPOINT" ? t("admittedEndpoint") : t("admittedEntity")}</label>
            <select
              id={subjectId}
              className="select-control"
              value={kind === "WAF_ENDPOINT" ? value.wafEndpointId : value.ipsEntityId}
              disabled={!parentSourceId || loading}
              onChange={(event) => {
                if (kind === "WAF_ENDPOINT") {
                  const endpoint = endpoints.find((row) => row.id === event.target.value);
                  onChange({ ...value, wafEndpointId: event.target.value });
                  onLabel(endpoint ? endpointLabel(endpoint) : event.target.value);
                } else {
                  const entity = entities.find((row) => row.id === event.target.value);
                  onChange({ ...value, ipsEntityId: event.target.value });
                  onLabel(entity ? entityLabel(entity) : event.target.value);
                }
              }}
            >
              <option value="">
                {!parentSourceId
                  ? t("chooseSourceFirst")
                  : loading
                    ? t("loadingSubjects")
                    : t("chooseAdmitted", {
                        subject: kind === "WAF_ENDPOINT" ? t("endpoints") : t("entities"),
                      })}
              </option>
              {(kind === "WAF_ENDPOINT" ? endpoints : entities).map((subject) => (
                <option key={subject.id} value={subject.id}>
                  {kind === "WAF_ENDPOINT"
                    ? endpointLabel(subject as WafEndpointInventory)
                    : entityLabel(subject as IpsEntityInventory)}
                </option>
              ))}
            </select>
          </div>
          {parentSourceId && !loading && (kind === "WAF_ENDPOINT" ? endpoints : entities).length === 0 && (
            <div className="notice cr-empty-subject">
              <p>
                {t("noneAdmitted", {
                  subjects: kind === "WAF_ENDPOINT" ? t("endpoints") : t("entities"),
                })}
              </p>
              <p>
                {t("customSearchBefore")}<Link href="/searches/new">{t("customSearch")}</Link>.
              </p>
            </div>
          )}
        </>
      )}

      {kind === "PW_VALUE" && (
        <>
          <div className="cr-field">
            <label htmlFor={parentId}>{t("propertyWatch")}</label>
            <select
              id={parentId}
              className="select-control"
              value={watchId}
              disabled={loading && watches.length === 0}
              onChange={(event) => {
                setWatchId(event.target.value);
                setPinnedValues([]);
                onChange({ ...value, pwValueId: "" });
                onLabel("");
              }}
            >
              <option value="">{t("chooseWatchFirst")}</option>
              {filteredWatches.map((watch) => (
                <option key={watch.id} value={watch.id}>{watch.name}</option>
              ))}
            </select>
          </div>
          <div className="cr-field">
            <label htmlFor={subjectId}>{t("pinnedValue")}</label>
            <select
              id={subjectId}
              className="select-control"
              value={value.pwValueId}
              disabled={!watchId || loading}
              onChange={(event) => {
                const chosen = pinnedValues.find((item) => item.id === event.target.value);
                onChange({ ...value, pwValueId: event.target.value });
                onLabel(chosen?.components.join(" × ") ?? "");
              }}
            >
              <option value="">
                {!watchId ? t("chooseWatchFirst") : loading ? t("loadingPinned") : t("choosePinned")}
              </option>
              {pinnedValues.map((item) => (
                <option key={item.id} value={item.id}>{item.components.join(" × ") || item.value_key}</option>
              ))}
            </select>
          </div>
          {watchId && !loading && pinnedValues.length === 0 && (
            <div className="notice cr-empty-subject">
              <p>
                {t("noPinnedBefore")}<Link href={`/behavior/watches/${watchId}`}>{t("openWatch")}</Link>{t("noPinnedAfter")}
              </p>
              <p>
                {t("newPin", { availability })}
              </p>
            </div>
          )}
          {watchId && pinnedValues.length > 0 && (
            <p className="field-hint">
              {t("onlyPinnedBefore")}<Link href={`/behavior/watches/${watchId}`}>{t("pinFirst")}</Link>
              {t("onlyPinnedAfter", { availability })}
            </p>
          )}
        </>
      )}

      {state === "error" && (
        <p className="field-error" role="alert">
          {t("loadFailed")}
        </p>
      )}
    </div>
  );
}
