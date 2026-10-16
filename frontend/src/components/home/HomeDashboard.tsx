"use client";

// The Home (UR.4): the user's dashboard at `/`, rendered through `DashboardView` (UR.5).
//
// Which dashboard (UI-4.1):
//   /?dashboard=<id>     that dashboard (a 404 says so and links to the start page)
//   /?dashboard=template the SOC Overview template, unsaved (the way back to it when a default
//                        is set; `/` alone shows the default)
//   /                    the dashboard marked default, else the template
// The template is also what a failed list read falls back to, with a line saying so.
//
// The widgets of the SOC Overview template (`widgets/templates.ts`):
//   KPI row (4)
//   Fleet EPS (8)                     | EPS by source class (4)
//   Top sources (4) | Severity (4)    | Log-source health (4)
//   Recent anomalies (6)              | Recent offenses (6)
//
// Every read goes through `useApiResource`, so the shell's refresh tick refreshes them all and
// each widget renders its own states: one refused or failed read leaves the others standing.
// A refresh keeps the dashboard in memory: `DashboardView` is keyed by the dashboard, not by
// the answer. After a write the list read is repeated under a new key; the server is the source
// of truth, and what the list held stays on screen until the new answer lands.

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { PageHeader } from "@/components/ui/PageHeader";
import { useReportDataAge } from "@/components/shell/AutoRefresh";
import { useApiResource, type ResourceState } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import {
  dashboardReads,
  readLayout,
  type DashboardListItem,
  type DashboardOut,
} from "@/lib/dashboardApi";

import {
  DashboardMenu,
  dashboardAccess,
  PERM_WRITE,
  type Caller,
  type CurrentDashboard,
  type DashboardChange,
} from "@/widgets/DashboardMenu";
import { DashboardSwitcher, type DashboardTarget } from "@/widgets/DashboardSwitcher";
import { DashboardView } from "@/widgets/DashboardView";
import {
  ConflictDialog,
  NameTakenDialog,
  UnsavedChangesDialog,
} from "@/widgets/DashboardSaveDialogs";
import { columnsForWidth, type GridColumns } from "@/widgets/layout";
import { SOC_OVERVIEW, templateByKey } from "@/widgets/templates";
import {
  useDashboard,
  type DashboardEditorSource,
  type SaveOutcome,
} from "@/widgets/useDashboard";
import type { DashboardLayout } from "@/widgets/types";

import "./dashboard.css";

/** The `dashboard` value that names the template (a saved dashboard's id is a UUID). */
export const TEMPLATE_PARAM = "template";

const NBSP = "\u00a0";

const TEMPLATE_DESCRIPTION =
  "24 h view of fleet volume, source health, anomalies and offenses. Times are UTC.";

/** Why a read failed, in words: the status when the server answered, else that it did not. */
function readFailure(state: ResourceState<unknown>): string {
  if (state.status === "forbidden") return "your role cannot read dashboards (permission read:dashboards)";
  if (state.status === "error") {
    return state.httpStatus === null
      ? "the server could not be reached"
      : `the server answered HTTP ${state.httpStatus}`;
  }
  return "the read has not finished";
}

export function dashboardHref(target: DashboardTarget, hasDefault: boolean): string {
  if (target.kind === "saved") return `/?dashboard=${encodeURIComponent(target.id)}`;
  return hasDefault ? `/?dashboard=${TEMPLATE_PARAM}` : "/";
}

/** The grid before the dashboard to show is known: the template's geometry, empty. */
function GridSkeleton() {
  const ref = useRef<HTMLDivElement>(null);
  const [cols, setCols] = useState<GridColumns>(12);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = (width: number) => {
      if (width > 0) setCols(columnsForWidth(width));
    };
    measure(el.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (entry) measure(entry.contentRect.width);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const kinds = ["kpi", "kpi", "kpi", "kpi", "wide", "third", "tall", "tall", "tall", "half", "half"];
  return (
    <div ref={ref} className="dg-measure" data-cols={cols} aria-busy="true">
      <p className="sr-only" role="status">
        Loading dashboard…
      </p>
      <div className="dg" data-cols={cols} aria-hidden="true">
        {kinds.map((kind, index) => (
          <div key={index} className="dg-skel" data-kind={kind} />
        ))}
      </div>
    </div>
  );
}

function Problem({ children }: { children: ReactNode }) {
  return (
    <div className="notice dg-problem" role="alert">
      {children}
    </div>
  );
}

interface DashboardSessionProps {
  source: DashboardEditorSource;
  layout: DashboardLayout;
  caller: Caller;
  items: readonly DashboardListItem[] | null;
  listLoading: boolean;
  listFailure: string | null;
  defaultItem: DashboardListItem | null;
  refused: string | null;
  description: string | null;
  notice?: ReactNode;
  onChanged: (change: DashboardChange) => void;
  onRefused: (detail: string) => void;
}

/** A loaded dashboard: one reducer owns its saved layout, current layout and every save path. */
function DashboardSession({
  source,
  layout,
  caller,
  items,
  listLoading,
  listFailure,
  defaultItem,
  refused,
  description,
  notice = null,
  onChanged,
  onRefused,
}: DashboardSessionProps) {
  const router = useRouter();
  const [nameRefusal, setNameRefusal] = useState<string | null>(null);
  const [conflictOpen, setConflictOpen] = useState(false);
  const [guarded, setGuarded] = useState<{
    action: () => void;
    focus: HTMLElement | null;
  } | null>(null);
  const editor = useDashboard(source, layout, {
    setDefaultOnCreate: defaultItem === null,
    onCreated: (dashboard) => {
      onChanged({ go: { kind: "saved", id: dashboard.id } });
    },
    onSaved: () => onChanged({ go: null }),
  });
  const state = editor.state;
  const syncMetadata = editor.syncMetadata;
  const currentSource = state.source;
  const listed =
    currentSource.kind === "saved"
      ? items?.find((item) => item.id === currentSource.dashboard.id) ?? null
      : null;
  useEffect(() => {
    if (listed) syncMetadata(listed);
  }, [listed, syncMetadata]);
  const current: CurrentDashboard =
    currentSource.kind === "template"
      ? { kind: "template" }
      : {
          kind: "saved",
          id: currentSource.dashboard.id,
          name: currentSource.dashboard.name,
          ownerSubject: currentSource.dashboard.owner_subject,
          visibility: currentSource.dashboard.visibility,
          isDefault: currentSource.dashboard.is_default,
          templateKey: currentSource.dashboard.template_key,
        };
  const access = dashboardAccess(current, caller, refused);
  const mayWrite =
    refused === null &&
    caller.status === "ok" &&
    caller.permissions.has(PERM_WRITE) &&
    (current.kind === "template" || current.ownerSubject === caller.subject);
  const name =
    currentSource.kind === "template"
      ? currentSource.template.name
      : currentSource.dashboard.name;
  const templateShown = currentSource.kind === "template";
  // A dashboard made from a template, with no description of its own, says what the template
  // holds and which widget was left out. The line takes the row the header reserves for its
  // qualifiers, so nothing below it moves.
  const templateText =
    currentSource.kind === "saved" && !description
      ? templateByKey(currentSource.dashboard.template_key)?.description
      : undefined;

  const requestNavigation = useCallback(
    (action: () => void) => {
      if (!state.dirty) return action();
      setGuarded({ action, focus: document.activeElement as HTMLElement | null });
    },
    [state.dirty],
  );

  // The shell is outside the allowed files. A capture listener on this page guards same-tab
  // sidebar links without modifying it; dashboard links remain owned by their own controls.
  useEffect(() => {
    if (!state.dirty) return;
    const capture = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) {
        return;
      }
      const target = event.target instanceof Element ? event.target.closest("a[href]") : null;
      if (!(target instanceof HTMLAnchorElement) || target.closest(".home") || target.target === "_blank") {
        return;
      }
      const url = new URL(target.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      event.preventDefault();
      event.stopPropagation();
      requestNavigation(() => router.push(`${url.pathname}${url.search}${url.hash}`));
    };
    document.addEventListener("click", capture, true);
    return () => document.removeEventListener("click", capture, true);
  }, [requestNavigation, router, state.dirty]);

  const handleSave = async (nameOverride?: string): Promise<boolean> => {
    const outcome: SaveOutcome = await editor.save(nameOverride);
    if (outcome.kind === "saved") {
      setNameRefusal(null);
      return true;
    }
    if (outcome.kind === "conflict") setConflictOpen(true);
    if (outcome.kind === "name") setNameRefusal(outcome.message);
    if (outcome.kind === "forbidden") onRefused(outcome.message);
    return false;
  };

  const switcher = (
    <DashboardSwitcher
      items={items}
      loading={listLoading}
      failure={listFailure}
      subject={caller.status === "ok" ? caller.subject : null}
      current={
        currentSource.kind === "saved"
          ? { kind: "saved", id: currentSource.dashboard.id }
          : { kind: "template" }
      }
      onSelect={(target) =>
        requestNavigation(() => router.push(dashboardHref(target, defaultItem !== null)))
      }
    />
  );
  const leading = (customizing: boolean) => (
    <>
      {switcher}
      <DashboardMenu
        current={current}
        access={access}
        customizing={customizing}
        onResetLayout={editor.changeLayout}
        onRequestNavigation={requestNavigation}
        onChanged={onChanged}
        onRefused={onRefused}
      />
    </>
  );
  const meta = (
    <>
      {templateShown ? (
        <>
          <span className="badge">Template, not saved</span>
          <span className="dg-qualifier">{TEMPLATE_DESCRIPTION}</span>
        </>
      ) : null}
      {current.kind === "saved" && current.isDefault ? <span className="badge">Default</span> : null}
      {current.kind === "saved" && current.visibility === "SHARED" ? (
        <span className="badge">Shared</span>
      ) : null}
      {access.note ? <span className="dg-qualifier">{access.note}</span> : null}
      {templateText ? (
        <span className="dg-template-desc" title={templateText}>
          {templateText}
        </span>
      ) : null}
    </>
  );
  // A meta row that renders empty still occupies its min-height and separates the title from
  // the dashboard's description, so state here what it can hold instead of passing it blindly.
  const hasMeta =
    templateShown ||
    (current.kind === "saved" && (current.isDefault || current.visibility === "SHARED")) ||
    access.note !== null ||
    templateText !== undefined;

  return (
    <>
      <DashboardView
        layout={state.currentLayout}
        onLayoutChange={editor.changeLayout}
        saved={!templateShown}
        dirty={state.dirty}
        canSave={mayWrite}
        saveOnDone={templateShown}
        saving={state.saveState === "saving"}
        saveMessage={state.saveState === "saved" || state.saveState === "error" ? state.message : null}
        onSave={() => handleSave()}
        onDiscard={editor.discard}
        leading={leading}
        renderHeader={(actions) => (
          <>
            <PageHeader
              title={name}
              description={templateShown ? undefined : description ?? undefined}
              meta={hasMeta ? meta : undefined}
              actions={actions}
            />
            {notice}
          </>
        )}
      />
      {nameRefusal ? (
        <NameTakenDialog
          initialName={name}
          serverMessage={nameRefusal}
          busy={state.saveState === "saving"}
          onClose={() => setNameRefusal(null)}
          onSave={async (nextName) => {
            const outcome = await editor.save(nextName);
            if (outcome.kind === "saved") {
              setNameRefusal(null);
              return null;
            }
            if (outcome.kind === "forbidden") onRefused(outcome.message);
            return "message" in outcome ? outcome.message : "The dashboard could not be saved.";
          }}
        />
      ) : null}
      {conflictOpen && currentSource.kind === "saved" ? (
        <ConflictDialog
          dashboardName={currentSource.dashboard.name}
          busy={state.saveState === "saving"}
          onClose={() => setConflictOpen(false)}
          onReload={async () => {
            if (await editor.reload()) setConflictOpen(false);
          }}
          onSaveCopy={async (copyName) => {
            const outcome = await editor.saveAsCopy(copyName);
            if (outcome.kind === "saved") {
              setConflictOpen(false);
              return null;
            }
            if (outcome.kind === "forbidden") onRefused(outcome.message);
            return "message" in outcome ? outcome.message : "The copy could not be saved.";
          }}
        />
      ) : null}
      {guarded ? (
        <UnsavedChangesDialog
          dashboardName={name}
          onKeep={() => {
            const focus = guarded.focus;
            setGuarded(null);
            queueMicrotask(() => focus?.focus());
          }}
          onDiscard={() => {
            const action = guarded.action;
            editor.discard();
            setGuarded(null);
            action();
          }}
        />
      ) : null}
    </>
  );
}

export function HomeDashboard() {
  const router = useRouter();
  const wanted = useSearchParams()?.get("dashboard") ?? null;
  const [rev, setRev] = useState(0);
  const [refused, setRefused] = useState<string | null>(null);

  const series = homeReads.epsSeries("24h", "none");
  const seriesState = useApiResource(series.key, series.fetch);
  // Only a series that was read says how far the data reaches; anything else is unknown.
  useReportDataAge(seriesState.status === "ok" ? seriesState.data.window_end : null);

  const listRead = dashboardReads.list();
  const list = useApiResource(rev === 0 ? listRead.key : `${listRead.key}&read=${rev}`, listRead.fetch);
  const meRead = homeReads.me();
  const me = useApiResource(meRead.key, meRead.fetch);

  // What the list held stays up while it is read again; a failed read holds nothing.
  const held = useRef<readonly DashboardListItem[] | null>(null);
  if (list.status === "ok") held.current = list.data.items;
  const items =
    list.status === "ok"
      ? list.data.items
      : list.status === "loading" || list.status === "idle"
        ? held.current
        : null;
  const listFailed = list.status === "error" || list.status === "forbidden";
  const defaultItem = items?.find((item) => item.is_default) ?? null;

  // Which dashboard: the one asked for, else the default, else the template.
  let savedId: string | null = null;
  let showTemplate = false;
  if (wanted === TEMPLATE_PARAM) showTemplate = true;
  else if (wanted) savedId = wanted;
  else if (defaultItem) savedId = defaultItem.id;
  else if (items || listFailed) showTemplate = true;

  const detailRead = savedId ? dashboardReads.one(savedId) : null;
  const detail = useApiResource(detailRead?.key ?? null, (signal) => {
    if (!detailRead) throw new Error("no dashboard to read");
    return detailRead.fetch(signal);
  });
  const dashboard: DashboardOut | null = detail.status === "ok" ? detail.data : null;
  const item = savedId ? (items?.find((entry) => entry.id === savedId) ?? null) : null;

  const caller: Caller =
    me.status === "ok"
      ? { status: "ok", subject: me.data.subject, permissions: new Set(me.data.effective_permissions) }
      : me.status === "loading" || me.status === "idle"
        ? { status: "loading" }
        : { status: "failed", reason: readFailure(me) };

  const known = item ?? dashboard;
  const current: CurrentDashboard | null = showTemplate
    ? { kind: "template" }
    : savedId && known
      ? {
          kind: "saved",
          id: savedId,
          // The list is re-read after a write and the dashboard is not, so the list's name wins.
          name: known.name,
          ownerSubject: known.owner_subject,
          visibility: known.visibility,
          isDefault: item ? item.is_default : (dashboard?.is_default ?? false),
        }
      : null;
  const access = current ? dashboardAccess(current, caller, refused) : { actions: [], note: null };

  const changed = (change: DashboardChange) => {
    if (change.go === "start") held.current = null;
    setRev((value) => value + 1);
    if (change.go === "start") router.push("/");
    else if (change.go) router.push(dashboardHref(change.go, true));
  };

  const switcher = (
    <DashboardSwitcher
      items={items}
      loading={!items && !listFailed}
      failure={listFailed ? readFailure(list) : null}
      subject={caller.status === "ok" ? caller.subject : null}
      current={savedId ? { kind: "saved", id: savedId } : { kind: "template" }}
      onSelect={(target) => router.push(dashboardHref(target, defaultItem !== null))}
    />
  );
  const menu = current ? (
    <DashboardMenu
      current={current}
      access={access}
      onChanged={changed}
      onRefused={(detail) => setRefused(detail)}
    />
  ) : null;

  const title = showTemplate ? SOC_OVERVIEW.name : (known?.name ?? "Dashboard");
  const meta = (
    <>
      {showTemplate ? (
        <>
          <span className="badge">Template, not saved</span>
          <span className="dg-qualifier">{TEMPLATE_DESCRIPTION}</span>
        </>
      ) : null}
      {current?.kind === "saved" && current.isDefault ? <span className="badge">Default</span> : null}
      {current?.kind === "saved" && current.visibility === "SHARED" ? (
        <span className="badge">Shared</span>
      ) : null}
      {access.note ? <span className="dg-qualifier">{access.note}</span> : null}
    </>
  );
  const hasMeta =
    showTemplate ||
    (current?.kind === "saved" && (current.isDefault || current.visibility === "SHARED")) ||
    access.note !== null;

  // `reserve` keeps the qualifier row while the dashboard is read, so the grid below does not
  // move when it arrives. A saved dashboard's own description, when it has one, is its own line.
  // The switcher and menu lead the actions; `DashboardView` places them itself (`lead` false).
  const header = (
    actions: ReactNode,
    description?: string | null,
    reserve = false,
    lead = true,
  ) => (
    <PageHeader
      title={title}
      description={description ?? undefined}
      meta={reserve ? <span aria-hidden="true">{NBSP}</span> : hasMeta ? meta : undefined}
      actions={
        <>
          {lead ? switcher : null}
          {lead ? menu : null}
          {actions}
        </>
      }
    />
  );

  let body: ReactNode;
  if (showTemplate) {
    const notice =
      listFailed && wanted !== TEMPLATE_PARAM ? (
        <p className="dg-status" role="status">
          Saved dashboards could not be loaded: {readFailure(list)}. This is the SOC Overview
          template, and changes here are not saved.
        </p>
      ) : null;
    body = (
      <DashboardSession
        key="template"
        source={{ kind: "template", template: SOC_OVERVIEW }}
        layout={SOC_OVERVIEW.layout}
        caller={caller}
        items={items}
        listLoading={!items && !listFailed}
        listFailure={listFailed ? readFailure(list) : null}
        defaultItem={defaultItem}
        refused={refused}
        description={null}
        notice={notice}
        onChanged={changed}
        onRefused={setRefused}
      />
    );
  } else if (savedId && detail.status === "ok") {
    const read = readLayout(detail.data.layout);
    if (read.ok) {
      body = (
        <DashboardSession
          key={savedId}
          source={{ kind: "saved", dashboard: detail.data }}
          layout={read.layout}
          caller={caller}
          items={items}
          listLoading={!items && !listFailed}
          listFailure={listFailed ? readFailure(list) : null}
          defaultItem={defaultItem}
          refused={refused}
          description={detail.data.description}
          onChanged={changed}
          onRefused={setRefused}
        />
      );
    } else {
      body = (
        <>
          {header(null, null)}
          <Problem>
            <p>
              <strong>This dashboard&apos;s layout cannot be shown.</strong>
            </p>
            <p>{read.reason} Nothing was changed; you can still rename or delete it.</p>
            <p className="notice-actions">
              <Link className="action secondary" href={dashboardHref({ kind: "template" }, true)}>
                Open the SOC Overview template
              </Link>
            </p>
          </Problem>
        </>
      );
    }
  } else if (savedId && (detail.status === "error" || detail.status === "forbidden")) {
    const notFound = detail.status === "error" && detail.httpStatus === 404;
    body = (
      <>
        {header(null, null)}
        <Problem>
          {notFound ? (
            <>
              <p>
                <strong>This dashboard does not exist, or it is not shared with you.</strong>
              </p>
              <p>It may have been deleted or made private since the link was made.</p>
              <p className="notice-actions">
                <Link className="action secondary" href="/">
                  {defaultItem ? "Open your default dashboard" : "Open the SOC Overview template"}
                </Link>
              </p>
            </>
          ) : (
            <>
              <p>
                <strong>This dashboard could not be loaded.</strong>
              </p>
              <p>
                {readFailure(detail)}. Nothing was changed. Refresh to try again, or open the
                template.
              </p>
              <p className="notice-actions">
                <Link className="action secondary" href={dashboardHref({ kind: "template" }, true)}>
                  Open the SOC Overview template
                </Link>
              </p>
            </>
          )}
        </Problem>
      </>
    );
  } else {
    body = (
      <>
        {header(null, null, true)}
        <GridSkeleton />
      </>
    );
  }

  return <div className="home">{body}</div>;
}
