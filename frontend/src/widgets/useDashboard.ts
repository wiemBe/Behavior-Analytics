"use client";

// One dashboard editing state (UI-4.2). The reducer owns both the last server-confirmed layout
// and the current layout. Every widget edit flows through `changeLayout`; a failed or stale write
// therefore changes only the save state and never discards the analyst's work.

import { useCallback, useEffect, useReducer } from "react";

import { ApiError } from "@/lib/api";
import {
  createDashboard,
  describeRefusal,
  getDashboard,
  readLayout,
  replaceDashboard,
  setDefaultDashboard,
  type DashboardOut,
} from "@/lib/dashboardApi";

import type { DescribedDashboardTemplate } from "./templates";
import type { DashboardLayout } from "./types";

export type DashboardSaveState = "idle" | "saving" | "saved" | "conflict" | "error";

export type DashboardEditorSource =
  | { kind: "template"; template: DescribedDashboardTemplate }
  | { kind: "saved"; dashboard: DashboardOut };

export interface DashboardEditorState {
  source: DashboardEditorSource;
  savedLayout: DashboardLayout;
  currentLayout: DashboardLayout;
  dirty: boolean;
  saveState: DashboardSaveState;
  message: string | null;
  currentRevision: number | null;
  errorStatus: number | null;
}

export type DashboardEditorAction =
  | { type: "layout"; layout: DashboardLayout }
  | { type: "discard" }
  | { type: "saving" }
  | { type: "saved"; dashboard: DashboardOut; layout: DashboardLayout }
  | { type: "conflict"; message: string; currentRevision: number }
  | { type: "error"; message: string; status: number | null }
  | {
      type: "metadata";
      name: string;
      visibility: DashboardOut["visibility"];
      isDefault: boolean;
      templateKey: string | null;
    }
  | { type: "reload"; dashboard: DashboardOut; layout: DashboardLayout };

function cloneLayout(layout: DashboardLayout): DashboardLayout {
  return {
    ...layout,
    widgets: layout.widgets.map((widget) => ({
      ...widget,
      config: { ...widget.config },
    })),
  };
}

/** Structural equality: object key insertion order and reference identity are irrelevant. */
export function layoutsEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (typeof left !== typeof right || left === null || right === null) return false;
  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false;
    return left.every((value, index) => layoutsEqual(value, right[index]));
  }
  if (typeof left !== "object" || typeof right !== "object") return false;
  const leftObject = left as Record<string, unknown>;
  const rightObject = right as Record<string, unknown>;
  const leftKeys = Object.keys(leftObject).sort();
  const rightKeys = Object.keys(rightObject).sort();
  return (
    layoutsEqual(leftKeys, rightKeys) &&
    leftKeys.every((key) => layoutsEqual(leftObject[key], rightObject[key]))
  );
}

export function initialDashboardState(
  source: DashboardEditorSource,
  layout: DashboardLayout,
): DashboardEditorState {
  const copy = cloneLayout(layout);
  return {
    source,
    savedLayout: copy,
    currentLayout: cloneLayout(copy),
    dirty: false,
    saveState: "idle",
    message: null,
    currentRevision: null,
    errorStatus: null,
  };
}

export function dashboardReducer(
  state: DashboardEditorState,
  action: DashboardEditorAction,
): DashboardEditorState {
  switch (action.type) {
    case "layout": {
      const layout = cloneLayout(action.layout);
      return {
        ...state,
        currentLayout: layout,
        dirty: !layoutsEqual(layout, state.savedLayout),
        saveState: "idle",
        message: null,
        currentRevision: null,
        errorStatus: null,
      };
    }
    case "discard":
      return {
        ...state,
        currentLayout: cloneLayout(state.savedLayout),
        dirty: false,
        saveState: "idle",
        message: null,
        currentRevision: null,
        errorStatus: null,
      };
    case "saving":
      return { ...state, saveState: "saving", message: null, errorStatus: null };
    case "saved": {
      const layout = cloneLayout(action.layout);
      return {
        source: { kind: "saved", dashboard: action.dashboard },
        savedLayout: layout,
        currentLayout: cloneLayout(layout),
        dirty: false,
        saveState: "saved",
        message: "Saved",
        currentRevision: null,
        errorStatus: null,
      };
    }
    case "conflict":
      return {
        ...state,
        saveState: "conflict",
        message: action.message,
        currentRevision: action.currentRevision,
        errorStatus: 409,
      };
    case "error":
      return {
        ...state,
        saveState: "error",
        message: action.message,
        currentRevision: null,
        errorStatus: action.status,
      };
    case "metadata":
      return state.source.kind === "template"
        ? state
        : {
            ...state,
            source: {
              kind: "saved",
              dashboard: {
                ...state.source.dashboard,
                name: action.name,
                visibility: action.visibility,
                is_default: action.isDefault,
                template_key: action.templateKey,
              },
            },
          };
    case "reload": {
      const layout = cloneLayout(action.layout);
      return {
        source: { kind: "saved", dashboard: action.dashboard },
        savedLayout: layout,
        currentLayout: cloneLayout(layout),
        dirty: false,
        saveState: "idle",
        message: null,
        currentRevision: null,
        errorStatus: null,
      };
    }
  }
}

export type SaveOutcome =
  | { kind: "saved"; dashboard: DashboardOut }
  | { kind: "conflict" }
  | { kind: "name"; message: string }
  | { kind: "forbidden"; message: string }
  | { kind: "error"; message: string };

export interface UseDashboardOptions {
  setDefaultOnCreate: boolean;
  onCreated?: (dashboard: DashboardOut) => void;
  onSaved?: (dashboard: DashboardOut) => void;
}

function statusOf(error: unknown): number | null {
  return error instanceof ApiError ? error.status : null;
}

export function useDashboard(
  source: DashboardEditorSource,
  layout: DashboardLayout,
  options: UseDashboardOptions,
) {
  const [state, dispatch] = useReducer(dashboardReducer, undefined, () =>
    initialDashboardState(source, layout),
  );

  useEffect(() => {
    if (!state.dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [state.dirty]);

  const save = useCallback(
    async (name?: string): Promise<SaveOutcome> => {
      dispatch({ type: "saving" });
      try {
        if (state.source.kind === "template") {
          const template = state.source.template;
          const dashboard = await createDashboard({
            name: name ?? template.name,
            description: template.description,
            layout: state.currentLayout,
            template_key: template.key,
            template_version: template.version,
          });
          dispatch({ type: "saved", dashboard, layout: state.currentLayout });
          if (options.setDefaultOnCreate) {
            // The dashboard exists; a refused default is not a failed save. The menu can set it.
            await setDefaultDashboard(dashboard.id).catch(() => undefined);
          }
          options.onCreated?.(dashboard);
          return { kind: "saved", dashboard };
        }

        const saved = state.source.dashboard;
        const result = await replaceDashboard(saved.id, {
          name: saved.name,
          description: saved.description,
          layout: state.currentLayout,
          expected_revision: saved.revision,
        });
        if (result.kind === "conflict") {
          dispatch({
            type: "conflict",
            message: result.message,
            currentRevision: result.current_revision,
          });
          return { kind: "conflict" };
        }
        dispatch({ type: "saved", dashboard: result.dashboard, layout: state.currentLayout });
        options.onSaved?.(result.dashboard);
        return { kind: "saved", dashboard: result.dashboard };
      } catch (error) {
        const message = describeRefusal(error);
        const status = statusOf(error);
        const display = `${message} Saving failed; your changes are still here.`;
        dispatch({ type: "error", message: display, status });
        if (status === 403) return { kind: "forbidden", message };
        if (state.source.kind === "template" && status === 409) {
          return { kind: "name", message };
        }
        return { kind: "error", message };
      }
    }, [options, state.currentLayout, state.source]);

  const reload = useCallback(async (): Promise<boolean> => {
    if (state.source.kind !== "saved") return false;
    try {
      const dashboard = await getDashboard(state.source.dashboard.id);
      const read = readLayout(dashboard.layout);
      if (!read.ok) throw new Error(read.reason);
      dispatch({ type: "reload", dashboard, layout: read.layout });
      options.onSaved?.(dashboard);
      return true;
    } catch (error) {
      dispatch({
        type: "error",
        message: `${describeRefusal(error)} Your unsaved changes are still here.`,
        status: statusOf(error),
      });
      return false;
    }
  }, [options, state.source]);

  const saveAsCopy = useCallback(
    async (name: string): Promise<SaveOutcome> => {
      dispatch({ type: "saving" });
      try {
        const from = state.source;
        const dashboard = await createDashboard({
          name,
          description:
            from.kind === "saved" ? from.dashboard.description : from.template.description,
          layout: state.currentLayout,
          template_key:
            from.kind === "saved" ? from.dashboard.template_key : from.template.key,
          template_version:
            from.kind === "saved" ? from.dashboard.template_version : from.template.version,
        });
        dispatch({ type: "saved", dashboard, layout: state.currentLayout });
        options.onCreated?.(dashboard);
        return { kind: "saved", dashboard };
      } catch (error) {
        const message = describeRefusal(error);
        const status = statusOf(error);
        dispatch({
          type: "error",
          message: `${message} Saving failed; your changes are still here.`,
          status,
        });
        if (status === 403) return { kind: "forbidden", message };
        return { kind: "error", message };
      }
    }, [options, state.currentLayout, state.source]);

  const syncMetadata = useCallback(
    (item: {
      name: string;
      visibility: DashboardOut["visibility"];
      is_default: boolean;
      template_key: string | null;
    }) =>
      dispatch({
        type: "metadata",
        name: item.name,
        visibility: item.visibility,
        isDefault: item.is_default,
        templateKey: item.template_key,
      }),
    [],
  );

  return {
    state,
    changeLayout: (next: DashboardLayout) => dispatch({ type: "layout", layout: next }),
    discard: () => dispatch({ type: "discard" }),
    syncMetadata,
    save,
    reload,
    saveAsCopy,
  };
}
