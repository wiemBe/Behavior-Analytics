"use client";

// Renders a layout (version 1) through the registry (UR.5.5). The grid is 12 columns when its
// container is wide, 6 or 1 below, derived from the stored layout; a widget box has a fixed
// height of `h x row + (h - 1) x gap`, from tokens, so the geometry is the same one the
// drag-and-resize grid will use. A body that is ever taller than its box scrolls inside the
// box; it is never clipped. Below 12 columns rows grow with their content, because that view
// is read-only and its widgets are narrower than their boxes were sized for.
//
// Customize mode (UR.5.3) edits the layout supplied by `useDashboard`: Add from the catalog,
// Remove, arrange, configure and per-widget range all report the same current layout upward.
// Editing is offered only at 12 columns. Expand stays local because it changes no layout.

import dynamic from "next/dynamic";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useContext,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type RefObject,
} from "react";

import { WidgetEditContext } from "@/components/home/WidgetFrame";
import { useInView } from "@/hooks/useInView";

import { Catalog, MAX_WIDGETS } from "./Catalog";
import { ConfigureDrawer, hasConfigure, type ConfigChoice } from "./Configure";
import { CadenceProvider } from "./cadence";
import { ExpandDialog } from "./Expand";
import {
  columnsForWidth,
  compactLayout,
  deriveLayout,
  firstFreeSpot,
  newWidgetId,
  type GridColumns,
} from "./layout";
import { placeholderFor } from "./Placeholder";
import { widgetDef } from "./registry";
import { effectiveRange, WidgetRuntimeContext, type WidgetRuntime } from "./runtime";

import type { AnyWidgetDef, DashboardLayout, LayoutWidget, WidgetRange } from "./types";

import "@/components/home/kpi.css";
import "@/components/home/tables.css";
import "./dashboard.css";

const EditGrid = dynamic(() => import("./Grid").then((module) => module.EditGrid), {
  ssr: false,
  loading: () => (
    <p className="dg-editor-loading" role="status">
      Loading dashboard editor…
    </p>
  ),
});

/** The grid's columns, from its own width (the sidebar takes a share of the viewport). */
function useGridColumns(ref: RefObject<HTMLElement | null>): GridColumns {
  const [cols, setCols] = useState<GridColumns>(12);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    // A width of 0 means nothing was measured (no layout yet): keep what is shown.
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
  }, [ref]);
  return cols;
}

/**
 * A custom property carrying one grid number; the stylesheet owns every length. The only inline
 * style in the widget system, accepted as an exception: unitless layout numbers, no colour and
 * no length (react-grid-layout will position with inline transforms anyway).
 */
function cell(w: LayoutWidget): CSSProperties {
  return {
    "--dg-x": w.x + 1,
    "--dg-y": w.y + 1,
    "--dg-w": w.w,
    "--dg-h": w.h,
  } as CSSProperties;
}

/** The frame of a widget that cannot show data: a type this version lacks, or a blocked one. */
function NoticeFrame({ id, title, text }: { id: string; title: string; text: string }) {
  const edit = useContext(WidgetEditContext);
  return (
    <section className="tw" aria-labelledby={`${id}-title`}>
      <header className={`tw-head${edit ? " dg-drag-handle" : ""}`}>
        <h2 className="tw-title" id={`${id}-title`}>
          {title}
        </h2>
        {edit?.arrange}
      </header>
      <div className="tw-body">
        <p className="tw-state" role="status">
          {text}
        </p>
      </div>
    </section>
  );
}

function PlacedWidget({
  widget,
  dashboardRange,
  onRemove,
  arrange,
  onRange,
  onExpand,
  onConfigure = null,
  expanded = false,
}: {
  widget: LayoutWidget;
  dashboardRange: WidgetRange;
  onRemove: (() => void) | null;
  arrange?: ReactNode;
  onRange: (range: WidgetRange) => void;
  /** Opens the expand dialog; null where Expand is not offered from the frame. */
  onExpand: ((trigger: HTMLElement) => void) | null;
  /** Opens the configure drawer; set in customize mode only. */
  onConfigure?: ((trigger: HTMLElement) => void) | null;
  expanded?: boolean;
}) {
  const def = widgetDef(widget.type);
  let content: ReactNode;
  if (!def) {
    content = (
      <NoticeFrame
        id={widget.id}
        title="Unsupported widget"
        text={`This dashboard holds a widget of type ${widget.type} that this version does not know. It is kept in the layout and nothing was changed.`}
      />
    );
  } else if (def.status === "blocked") {
    content = (
      <NoticeFrame
        id={widget.id}
        title={def.title}
        text={`Not available: ${def.blockedBy}.`}
      />
    );
  } else {
    const Component = def.Component;
    const runtime: WidgetRuntime = {
      rangeOptions: def.range,
      override: widget.range,
      dashboardRange,
      setRange: onRange,
      onExpand: def.expandable ? onExpand : null,
      expanded,
      onConfigure: hasConfigure(def) ? onConfigure : null,
    };
    content = (
      <WidgetRuntimeContext.Provider value={runtime}>
        <Component
          id={widget.id}
          config={widget.config}
          range={widget.range}
          expanded={expanded}
        />
      </WidgetRuntimeContext.Provider>
    );
  }
  return onRemove ? (
    <WidgetEditContext.Provider value={{ onRemove, arrange }}>{content}</WidgetEditContext.Provider>
  ) : (
    content
  );
}

/**
 * One cell of the read grid (UR.8.3). The box is drawn at once, at its grid size, so the layout
 * never shifts; its body is mounted only once the box has come near the viewport, so an off-screen
 * chart is never created. Until then the box holds the same title-and-loading-line placeholder the
 * widget shows while its own code loads — no skeleton.
 *
 * A box the layout already puts inside the viewport plus one viewport of margin mounts its body
 * in this cell's first render, which is the page's own commit: a widget body that first renders
 * any later does not appear until React's reveal throttle has run (`useInView`, from the row and
 * the row pitch, with no measurement). Boxes below the margin start as the placeholder and mount
 * when the observer answers.
 *
 * `defer` is true only at 12 columns, where a box's height comes from the grid (`h x row`), so
 * deferring the body cannot move anything. Below 12 columns the read-only view grows each row to
 * its content, so a short placeholder would shift the page as bodies mount; there everything is
 * mounted at once, and no box is observed at all. Customize mode does not use this cell: it renders
 * every widget, because dragging needs the real sizes.
 */
function PlacedCell({
  widget,
  dashboardRange,
  onRange,
  onExpand,
  defer,
}: {
  widget: LayoutWidget;
  dashboardRange: WidgetRange;
  onRange: (range: WidgetRange) => void;
  onExpand: ((trigger: HTMLElement) => void) | null;
  defer: boolean;
}) {
  const { ref, inView } = useInView<HTMLDivElement>(defer ? widget.y : null);
  const def = widgetDef(widget.type);
  // A stable placeholder for this type, so a re-render (a refresh tick) does not remount it.
  const Deferred = useMemo(
    () =>
      def && def.status === "live"
        ? placeholderFor(def.title, def.category === "KPI")
        : null,
    [def],
  );
  return (
    <div
      ref={ref}
      className="dg-item"
      data-type={widget.type}
      data-x={widget.x}
      data-y={widget.y}
      data-w={widget.w}
      data-h={widget.h}
      style={cell(widget)}
    >
      {!defer || inView || !Deferred ? (
        <PlacedWidget
          widget={widget}
          dashboardRange={dashboardRange}
          onRemove={null}
          onRange={onRange}
          onExpand={onExpand}
        />
      ) : (
        <Deferred />
      )}
    </div>
  );
}

export interface DashboardViewProps {
  /** The current layout. With `onLayoutChange`, this component is controlled by `useDashboard`. */
  layout: DashboardLayout;
  onLayoutChange?: (layout: DashboardLayout) => void;
  /** The page header, given the customize controls for its actions. */
  renderHeader: (actions: ReactNode) => ReactNode;
  /** True for a saved dashboard. */
  saved?: boolean;
  /**
   * Header controls that lead the actions (the dashboard switcher and menu). They are left out
   * while customizing: the session line takes their room, and switching away would drop the
   * changes in memory.
   */
  leading?: ReactNode | ((customizing: boolean) => ReactNode);
  dirty?: boolean;
  canSave?: boolean;
  saveOnDone?: boolean;
  saving?: boolean;
  saveMessage?: string | null;
  onSave?: () => Promise<boolean>;
  onDiscard?: () => void;
  onCustomizeChange?: (customizing: boolean) => void;
}

export function DashboardView({
  layout: initial,
  onLayoutChange,
  renderHeader,
  leading = null,
  dirty = false,
  canSave = false,
  saveOnDone = false,
  saving = false,
  saveMessage = null,
  onSave,
  onDiscard,
  onCustomizeChange,
}: DashboardViewProps) {
  const [localLayout, setLocalLayout] = useState(initial);
  const layout = onLayoutChange ? initial : localLayout;
  const [customizing, setCustomizing] = useState(false);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [configureId, setConfigureId] = useState<string | null>(null);
  // The control that opened the expand dialog or the drawer: focus returns to it on close.
  const trigger = useRef<HTMLElement | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);
  const cols = useGridColumns(gridRef);
  const editable = cols === 12;
  const editing = customizing && editable;
  const placed = deriveLayout(layout.widgets, cols);
  const expanded = layout.widgets.find((w) => w.id === expandedId) ?? null;
  const expandedDef = expanded ? widgetDef(expanded.type) : undefined;
  const configuring = layout.widgets.find((w) => w.id === configureId) ?? null;
  const configureDef = configuring ? widgetDef(configuring.type) : undefined;

  const updateLayout = useCallback(
    (update: DashboardLayout | ((current: DashboardLayout) => DashboardLayout)) => {
      const next = typeof update === "function" ? update(layout) : update;
      if (onLayoutChange) onLayoutChange(next);
      else setLocalLayout(next);
    },
    [layout, onLayoutChange],
  );

  const setCustomize = (value: boolean) => {
    setCustomizing(value);
    onCustomizeChange?.(value);
  };

  // Focus returns to the trigger once the catalog has closed.
  useEffect(() => {
    if (wasOpen.current && !catalogOpen) addRef.current?.focus();
    wasOpen.current = catalogOpen;
  }, [catalogOpen]);

  const remove = useCallback((id: string) => {
    updateLayout((l) => ({
      ...l,
      widgets: compactLayout(l.widgets.filter((widget) => widget.id !== id)),
    }));
    setNotice("Widget removed.");
  }, [updateLayout]);

  const setRange = useCallback((id: string, range: WidgetRange | null) => {
    updateLayout((l) => ({
      ...l,
      widgets: l.widgets.map((widget) => (widget.id === id ? { ...widget, range } : widget)),
    }));
  }, [updateLayout]);

  const apply = (id: string, choice: ConfigChoice) => {
    updateLayout((l) => ({
      ...l,
      widgets: l.widgets.map((widget) =>
        widget.id === id ? { ...widget, range: choice.range, config: choice.config } : widget,
      ),
    }));
    setNotice("Widget configuration applied.");
    closeOverlay(setConfigureId);
  };

  const openOverlay = (set: (id: string | null) => void, id: string, from: HTMLElement) => {
    trigger.current = from;
    set(id);
  };

  /** Closes the expand dialog or the drawer, then returns focus to what opened it. */
  function closeOverlay(set: (id: string | null) => void) {
    set(null);
    const from = trigger.current;
    trigger.current = null;
    // After the dialog has unmounted, so the browser's own focus restoration cannot win.
    queueMicrotask(() => from?.focus());
  }

  const add = (def: AnyWidgetDef) => {
    if (def.status !== "live" || layout.widgets.length >= MAX_WIDGETS) return;
    const { x, y } = firstFreeSpot(layout.widgets, def.size.w, def.size.h);
    const id = newWidgetId(new Set(layout.widgets.map((w) => w.id)));
    updateLayout({
      ...layout,
      widgets: [
        ...layout.widgets,
        {
          id,
          type: def.type,
          x,
          y,
          w: def.size.w,
          h: def.size.h,
          range: null,
          config: { ...def.defaultConfig },
        },
      ],
    });
    setNotice(`Added ${def.title}.`);
    setCatalogOpen(false);
  };

  const pending = (
    <>
      {dirty && canSave ? (
        <button type="button" className="action" disabled={saving} onClick={() => void onSave?.()}>
          {saving ? "Saving…" : "Save"}
        </button>
      ) : null}
      {dirty ? (
        <button type="button" className="action secondary" onClick={onDiscard}>
          Discard
        </button>
      ) : null}
    </>
  );
  const lead = typeof leading === "function" ? leading(customizing) : leading;
  let actions: ReactNode;
  if (!editable) {
    actions = (
      <>
        {lead}
        {pending}
        {dirty ? null : <p className="dg-hint">Widen the window to customize</p>}
      </>
    );
  } else if (!editing) {
    actions = (
      <>
        {lead}
        {pending}
        <button type="button" className="action secondary" onClick={() => setCustomize(true)}>
          Customize
        </button>
      </>
    );
  } else {
    actions = (
      <>
        {lead}
        <button
          type="button"
          ref={addRef}
          className="action secondary"
          onClick={() => setCatalogOpen(true)}
        >
          Add widget
        </button>
        <button
          type="button"
          className="action"
          disabled={saving}
          onClick={async () => {
            if ((dirty || saveOnDone) && canSave && onSave) {
              if (!(await onSave())) return;
            }
            setCustomize(false);
            setNotice("");
          }}
        >
          {saving ? "Saving…" : "Done"}
        </button>
      </>
    );
  }

  return (
    <CadenceProvider>
      {renderHeader(actions)}
      {dirty || saveMessage || notice ? (
        <p className="dg-status dg-save-status" role="status" aria-live="polite">
          {dirty
            ? canSave
              ? "Unsaved changes."
              : "Unsaved changes. They last for this session only."
            : null}
          {saveMessage ? `${dirty ? " " : ""}${saveMessage}` : null}
          {notice ? <span className="dg-notice"> {notice}</span> : null}
        </p>
      ) : null}
      <div ref={gridRef} className="dg-measure" data-cols={cols} data-editing={editing || undefined}>
        {editing ? (
          <EditGrid
            widgets={layout.widgets}
            onChange={(widgets) => updateLayout((current) => ({ ...current, widgets }))}
            onRemove={remove}
            onConfigure={(id, from) => openOverlay(setConfigureId, id, from)}
            onExpand={(id, from) => openOverlay(setExpandedId, id, from)}
            renderWidget={(widget, arrange) => (
              <PlacedWidget
                widget={widget}
                dashboardRange={layout.range}
                arrange={arrange}
                onRemove={() => remove(widget.id)}
                onRange={(range) => setRange(widget.id, range)}
                onExpand={null}
                onConfigure={(from) => openOverlay(setConfigureId, widget.id, from)}
              />
            )}
          />
        ) : (
          <div className="dg" data-cols={cols}>
            {placed.map((widget) => (
              <PlacedCell
                key={widget.id}
                widget={widget}
                dashboardRange={layout.range}
                onRange={(range) => setRange(widget.id, range)}
                onExpand={(from) => openOverlay(setExpandedId, widget.id, from)}
                defer={cols === 12}
              />
            ))}
          </div>
        )}
      </div>
      {expanded && expandedDef ? (
        <ExpandDialog
          title={expandedDef.title}
          range={effectiveRange(expanded.range, layout.range)}
          rangeOptions={expandedDef.range}
          onRange={(range) => setRange(expanded.id, range)}
          onClose={() => closeOverlay(setExpandedId)}
        >
          <PlacedWidget
            widget={expanded}
            dashboardRange={layout.range}
            onRemove={null}
            onRange={(range) => setRange(expanded.id, range)}
            onExpand={null}
            expanded
          />
        </ExpandDialog>
      ) : null}
      {configuring && configureDef ? (
        <ConfigureDrawer
          def={configureDef}
          widget={configuring}
          dashboardRange={layout.range}
          onApply={(choice) => apply(configuring.id, choice)}
          onClose={() => closeOverlay(setConfigureId)}
        />
      ) : null}
      {catalogOpen ? (
        <Catalog
          placedTypes={new Set(layout.widgets.map((w) => w.type))}
          placedCount={layout.widgets.length}
          onAdd={add}
          onClose={() => setCatalogOpen(false)}
        />
      ) : null}
    </CadenceProvider>
  );
}
