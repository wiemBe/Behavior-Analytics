"use client";

// The customize-mode grid. This module is loaded only after Customize is opened, keeping
// react-grid-layout and its pointer machinery out of the read-mode client bundle.

import ReactGridLayout, {
  useContainerWidth,
  verticalCompactor,
  type Layout as GridLayoutValue,
} from "react-grid-layout";
import {
  useCallback,
  useId,
  useLayoutEffect,
  useState,
  type ReactNode,
} from "react";

import {
  moveWidget,
  resizeWidget,
  type MoveDirection,
  type ResizeDirection,
} from "./layout";
import { widgetDef } from "./registry";
import { GRID_COLUMNS, type LayoutWidget, type WidgetSize } from "./types";

interface GridMetrics {
  rowHeight: number;
  gap: number;
}

function readMetrics(): GridMetrics | null {
  const styles = getComputedStyle(document.documentElement);
  const rowHeight = Number.parseFloat(styles.getPropertyValue("--dash-row"));
  const gap = Number.parseFloat(styles.getPropertyValue("--dash-gap"));
  return Number.isFinite(rowHeight) && Number.isFinite(gap) ? { rowHeight, gap } : null;
}

function gridLayout(widgets: readonly LayoutWidget[]): GridLayoutValue {
  return widgets.map((widget) => {
    const size = widgetDef(widget.type)?.size;
    return {
      i: widget.id,
      x: widget.x,
      y: widget.y,
      w: widget.w,
      h: widget.h,
      minW: size?.minW,
      minH: size?.minH,
      maxW: size?.maxW,
      maxH: size?.maxH,
      resizeHandles: ["se"],
    };
  });
}

function applyGridLayout(
  widgets: readonly LayoutWidget[],
  changed: GridLayoutValue,
): LayoutWidget[] {
  const positions = new Map(changed.map((item) => [item.i, item]));
  return widgets.map((widget) => {
    const item = positions.get(widget.id);
    return item
      ? { ...widget, x: item.x, y: item.y, w: item.w, h: item.h }
      : { ...widget };
  });
}

function sameGeometry(a: readonly LayoutWidget[], b: readonly LayoutWidget[]): boolean {
  return (
    a.length === b.length &&
    a.every((widget, index) => {
      const other = b[index];
      return (
        other?.id === widget.id &&
        other.x === widget.x &&
        other.y === widget.y &&
        other.w === widget.w &&
        other.h === widget.h
      );
    })
  );
}

interface ArrangeProps {
  title: string;
  widget: LayoutWidget;
  size: WidgetSize;
  onMove: (direction: MoveDirection) => void;
  onResize: (direction: ResizeDirection) => void;
  onRemove: () => void;
}

/** Compact on demand: the controls overlay the body instead of changing the widget's geometry. */
function ArrangeControls({ title, widget, size, onMove, onResize, onRemove }: ArrangeProps) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const atMaxW = widget.w >= (size.maxW ?? GRID_COLUMNS) || widget.x + widget.w >= GRID_COLUMNS;
  const atMaxH = size.maxH !== undefined && widget.h >= size.maxH;
  return (
    <div className="dg-arrange">
      <button
        type="button"
        className="dg-arrange-trigger"
        aria-label={`Arrange ${title}`}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((current) => !current)}
      >
        Edit
      </button>
      {open ? (
        <div className="dg-arrange-panel" id={panelId}>
          <fieldset>
            <legend>Move</legend>
            <div className="dg-step-grid">
              {(["left", "right", "up", "down"] as const).map((direction) => (
                <button
                  type="button"
                  className="dg-step"
                  key={direction}
                  aria-label={`Move ${title} ${direction}`}
                  disabled={
                    (direction === "left" && widget.x === 0) ||
                    (direction === "right" && widget.x + widget.w >= GRID_COLUMNS) ||
                    (direction === "up" && widget.y === 0)
                  }
                  onClick={() => onMove(direction)}
                >
                  {direction[0].toUpperCase() + direction.slice(1)}
                </button>
              ))}
            </div>
          </fieldset>
          <fieldset>
            <legend>Resize</legend>
            <div className="dg-step-grid">
              {(["wider", "narrower", "taller", "shorter"] as const).map((direction) => (
                <button
                  type="button"
                  className="dg-step"
                  key={direction}
                  aria-label={`Resize ${title} ${direction}`}
                  disabled={
                    (direction === "wider" && atMaxW) ||
                    (direction === "narrower" && widget.w <= size.minW) ||
                    (direction === "taller" && atMaxH) ||
                    (direction === "shorter" && widget.h <= size.minH)
                  }
                  onClick={() => onResize(direction)}
                >
                  {direction === "narrower"
                    ? "Narrow"
                    : direction === "shorter"
                      ? "Shorten"
                      : direction[0].toUpperCase() + direction.slice(1)}
                </button>
              ))}
            </div>
          </fieldset>
          <button
            type="button"
            className="dg-step dg-arrange-remove"
            aria-label={`Remove ${title}`}
            onClick={onRemove}
          >
            Remove widget
          </button>
        </div>
      ) : null}
    </div>
  );
}

export interface EditGridProps {
  widgets: readonly LayoutWidget[];
  onChange: (widgets: LayoutWidget[]) => void;
  onRemove: (id: string) => void;
  renderWidget: (widget: LayoutWidget, arrange: ReactNode) => ReactNode;
}

export function EditGrid({ widgets, onChange, onRemove, renderWidget }: EditGridProps) {
  const { width, containerRef, mounted } = useContainerWidth({ measureBeforeMount: true });
  const [metrics, setMetrics] = useState<GridMetrics | null>(null);

  useLayoutEffect(() => {
    setMetrics(readMetrics());
  }, []);

  const acceptGridChange = useCallback(
    (nextGrid: GridLayoutValue) => {
      const next = applyGridLayout(widgets, nextGrid);
      if (!sameGeometry(widgets, next)) onChange(next);
    },
    [onChange, widgets],
  );

  return (
    <div className="dg-edit-shell" ref={containerRef} data-editing="true">
      {mounted && metrics ? (
        <ReactGridLayout
          width={width}
          className="dg-edit"
          layout={gridLayout(widgets)}
          gridConfig={{
            cols: GRID_COLUMNS,
            rowHeight: metrics.rowHeight,
            margin: [metrics.gap, metrics.gap],
            containerPadding: [0, 0],
          }}
          dragConfig={{
            enabled: true,
            handle: ".dg-drag-handle",
            cancel: "a, button, input, select, textarea, summary",
          }}
          resizeConfig={{ enabled: true, handles: ["se"] }}
          compactor={verticalCompactor}
          onDragStop={acceptGridChange}
          onResizeStop={acceptGridChange}
        >
          {widgets.map((widget) => {
            const def = widgetDef(widget.type);
            const title = def?.title ?? "Unsupported widget";
            const size = def?.size ?? {
              w: widget.w,
              h: widget.h,
              minW: 1,
              minH: 1,
              maxW: GRID_COLUMNS,
            };
            const arrange = (
              <ArrangeControls
                title={title}
                widget={widget}
                size={size}
                onMove={(direction) => onChange(moveWidget(widgets, widget.id, direction))}
                onResize={(direction) =>
                  onChange(resizeWidget(widgets, widget.id, direction, size))
                }
                onRemove={() => onRemove(widget.id)}
              />
            );
            return (
              <div
                className="dg-item"
                key={widget.id}
                data-type={widget.type}
                data-x={widget.x}
                data-y={widget.y}
                data-w={widget.w}
                data-h={widget.h}
              >
                {renderWidget(widget, arrange)}
              </div>
            );
          })}
        </ReactGridLayout>
      ) : (
        <p className="dg-editor-loading" role="status">
          Loading dashboard editor…
        </p>
      )}
    </div>
  );
}
