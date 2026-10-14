"use client";

// What a widget shows while its code loads (UR.5.1): text only, no shimmer. The title is
// known from the registry, so the box already says what is coming; the widget's own frame
// replaces this once its component has loaded and reads its data.

import type { ReactElement } from "react";

import "@/components/home/kpi.css";
import "@/components/home/tables.css";

export function placeholderFor(title: string, kpi: boolean): () => ReactElement {
  function WidgetPlaceholder() {
    const Root = kpi ? "article" : "section";
    return (
      <Root className={kpi ? "kpi" : "tw"} aria-busy="true">
        <header className={kpi ? "kpi-head" : "tw-head"}>
          <h2 className={kpi ? "kpi-title" : "tw-title"}>{title}</h2>
        </header>
        <p className={kpi ? "kpi-state" : "tw-state"} role="status">
          Loading {title.toLowerCase()}…
        </p>
      </Root>
    );
  }
  return WidgetPlaceholder;
}
