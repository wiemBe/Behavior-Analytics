"use client";

// The dashboard switcher (UR.5.3): a disclosure listing the dashboards the caller can open, the
// ones they own and the shared ones, then the SOC Overview template. It lists what the list
// read returned and nothing else. A button and a list: `Esc` closes it and returns focus to the
// button; a click outside closes it; choosing an entry asks the page to navigate.

import { useEffect, useId, useRef, useState } from "react";

import type { DashboardListItem } from "@/lib/dashboardApi";

import "./dashboard.css";

/** Where the page goes: a saved dashboard by id, or the SOC Overview template. */
export type DashboardTarget = { kind: "template" } | { kind: "saved"; id: string };

/**
 * Open state for a button-and-panel disclosure: `Esc` closes and returns focus to the button,
 * and a press outside the root closes without moving focus.
 */
export function usePopover() {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      setOpen(false);
      trigger.current?.focus();
    };
    const onPress = (event: Event) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPress);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPress);
    };
  }, [open]);

  return { open, setOpen, root, trigger };
}

export interface DashboardSwitcherProps {
  /** The list read's items; null while there is none to show. */
  items: readonly DashboardListItem[] | null;
  /** True while the list is being read and nothing is held yet. */
  loading: boolean;
  /** Why the list could not be read, or null. */
  failure: string | null;
  /** The caller's subject when known, so another owner's dashboard names its owner. */
  subject: string | null;
  current: DashboardTarget;
  onSelect: (target: DashboardTarget) => void;
}

export function DashboardSwitcher({
  items,
  loading,
  failure,
  subject,
  current,
  onSelect,
}: DashboardSwitcherProps) {
  const { open, setOpen, root, trigger } = usePopover();
  const panelId = useId();
  const choose = (target: DashboardTarget) => {
    setOpen(false);
    trigger.current?.focus();
    onSelect(target);
  };
  const templateCurrent = current.kind === "template";

  return (
    <div className="dg-pop" ref={root}>
      <button
        type="button"
        ref={trigger}
        className="dg-trigger"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen(!open)}
      >
        Dashboards
      </button>
      {open ? (
        <div className="dg-pop-panel dg-switch-panel" id={panelId}>
          {loading ? (
            <p className="dg-pop-note" role="status">
              Loading dashboards…
            </p>
          ) : null}
          {failure ? (
            <p className="dg-pop-note" role="status">
              Saved dashboards could not be loaded: {failure}.
            </p>
          ) : null}
          {items && items.length === 0 ? (
            <p className="dg-pop-note">You have no saved dashboards, and none is shared with you.</p>
          ) : null}
          {items && items.length > 0 ? (
            <ul className="dg-pop-list" aria-label="Saved dashboards">
              {items.map((item) => {
                const mine = subject !== null && item.owner_subject === subject;
                const here = current.kind === "saved" && current.id === item.id;
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      className="dg-pop-item"
                      aria-current={here ? "true" : undefined}
                      onClick={() => choose({ kind: "saved", id: item.id })}
                    >
                      <span className="dg-pop-name">{item.name}</span>
                      <span className="dg-pop-marks">
                        {item.is_default ? <span className="badge">Default</span> : null}
                        {item.visibility === "SHARED" ? <span className="badge">Shared</span> : null}
                      </span>
                      <span className="dg-pop-sub">
                        {item.widget_count} {item.widget_count === 1 ? "widget" : "widgets"}
                        {subject !== null && !mine ? `, owned by ${item.owner_subject}` : ""}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
          <ul className="dg-pop-list dg-pop-template" aria-label="Template">
            <li>
              <button
                type="button"
                className="dg-pop-item"
                aria-current={templateCurrent ? "true" : undefined}
                onClick={() => choose({ kind: "template" })}
              >
                <span className="dg-pop-name">SOC Overview (template)</span>
                <span className="dg-pop-sub">Not saved</span>
              </button>
            </li>
          </ul>
        </div>
      ) : null}
    </div>
  );
}
