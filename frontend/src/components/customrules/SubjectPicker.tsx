"use client";

import Link from "next/link";
import { useEffect, useId, useMemo, useState } from "react";

import { SUBJECT_KIND_LABELS, subjectKindInSentence } from "@/components/customrules/authoring";
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
        <label htmlFor={kindId}>Subject kind</label>
        <select
          id={kindId}
          className="select-control"
          value={kind}
          onChange={(event) => onKind(event.target.value as CustomRuleSubjectKind)}
        >
          {catalog.subject_kinds.map((subject) => (
            <option key={subject.subject_kind} value={subject.subject_kind}>
              {SUBJECT_KIND_LABELS[subject.subject_kind]}
            </option>
          ))}
        </select>
      </div>

      {kind === "FLEET_CLASS" ? (
        <div className="cr-field">
          <label htmlFor={subjectId}>Fleet class</label>
          <select
            id={subjectId}
            className="select-control"
            value={value.sourceClass}
            onChange={(event) => {
              onChange({ ...value, sourceClass: event.target.value });
              onLabel(event.target.value);
            }}
          >
            <option value="">Choose a fleet class</option>
            {catalog.source_classes.map((sourceClass) => (
              <option key={sourceClass} value={sourceClass}>{sourceClass}</option>
            ))}
          </select>
        </div>
      ) : (
        <div className="cr-field">
          <label htmlFor={searchId}>
            Find {kind === "PW_VALUE" ? "property watch" : subjectKindInSentence(kind)}
          </label>
          <input
            id={searchId}
            className="field"
            type="search"
            value={query}
            placeholder={
              kind === "PW_VALUE"
                ? "Filter existing property watches"
                : "Filter the existing source list"
            }
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      )}

      {kind === "LOG_SOURCE" && (
        <div className="cr-field">
          <label htmlFor={subjectId}>Log source</label>
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
            <option value="">{loading ? "Loading sources…" : "Choose a log source"}</option>
            {filteredSources.map((source) => (
              <option key={source.log_source_id} value={source.log_source_id}>{source.name}</option>
            ))}
          </select>
        </div>
      )}

      {kind === "IPS_SOURCE" && (
        <div className="cr-field">
          <label htmlFor={subjectId}>IPS source</label>
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
            <option value="">{loading ? "Loading sources…" : "Choose an IPS source"}</option>
            {filteredIpsSources.map((source) => (
              <option key={source.log_source_id} value={source.log_source_id}>{source.log_source_name}</option>
            ))}
          </select>
        </div>
      )}

      {(kind === "WAF_ENDPOINT" || kind === "IPS_ENTITY") && (
        <>
          <div className="cr-field">
            <label htmlFor={parentId}>{kind === "WAF_ENDPOINT" ? "WAF source" : "IPS source"}</label>
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
              <option value="">Choose a source first</option>
              {(kind === "WAF_ENDPOINT" ? wafSources : filteredIpsSources).map((source) => (
                <option key={source.log_source_id} value={source.log_source_id}>
                  {source.log_source_name}
                </option>
              ))}
            </select>
          </div>
          <div className="cr-field">
            <label htmlFor={subjectId}>{kind === "WAF_ENDPOINT" ? "Admitted endpoint" : "Admitted entity"}</label>
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
                {!parentSourceId ? "Choose a source first" : loading ? "Loading subjects…" : `Choose an admitted ${kind === "WAF_ENDPOINT" ? "endpoint" : "entity"}`}
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
                No admitted {kind === "WAF_ENDPOINT" ? "endpoints" : "entities"} are available.
                Only admitted subjects can be used by a rule because the platform stores no
                measurement for anything else.
              </p>
              <p>
                Conditions the platform does not collect belong in a <Link href="/searches/new">custom search</Link>.
              </p>
            </div>
          )}
        </>
      )}

      {kind === "PW_VALUE" && (
        <>
          <div className="cr-field">
            <label htmlFor={parentId}>Property watch</label>
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
              <option value="">Choose a property watch first</option>
              {filteredWatches.map((watch) => (
                <option key={watch.id} value={watch.id}>{watch.name}</option>
              ))}
            </select>
          </div>
          <div className="cr-field">
            <label htmlFor={subjectId}>Pinned value</label>
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
                {!watchId ? "Choose a property watch first" : loading ? "Loading pinned values…" : "Choose a pinned value"}
              </option>
              {pinnedValues.map((item) => (
                <option key={item.id} value={item.id}>{item.components.join(" × ") || item.value_key}</option>
              ))}
            </select>
          </div>
          {watchId && !loading && pinnedValues.length === 0 && (
            <div className="notice cr-empty-subject">
              <p>
                No pinned value is available. Automatic values are not offered to a threshold
                rule: <Link href={`/behavior/watches/${watchId}`}>open the property watch</Link> and
                pin the value first.
              </p>
              <p>
                A new pin can be used in a rule after the next property-watch collection, within
                about an hour.
              </p>
            </div>
          )}
          {watchId && pinnedValues.length > 0 && (
            <p className="field-hint">
              Only pinned values are listed. To use another value, <Link href={`/behavior/watches/${watchId}`}>pin it on the watch first</Link>.
              A value pinned just now is listed after the next property-watch collection, within
              about an hour.
            </p>
          )}
        </>
      )}

      {state === "error" && (
        <p className="field-error" role="alert">
          The existing subject list could not be loaded. Check the backend, then choose the subject again.
        </p>
      )}
    </div>
  );
}
