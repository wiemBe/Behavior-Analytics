"use client";

// What a widget shows while its code loads (UR.5.1): text only, no shimmer. The title is
// known from the registry, so the box already says what is coming; the widget's own frame
// replaces this once its component has loaded and reads its data.

import { useLocale, useTranslations } from "next-intl";
import type { ReactElement } from "react";

import { useWidgetCopy } from "./copy";
import type { AnyWidgetDef } from "./types";

import "@/components/home/kpi.css";
import "@/components/home/tables.css";

export function placeholderFor(
  def: Pick<AnyWidgetDef, "type" | "title">,
  kpi: boolean,
): () => ReactElement {
  function WidgetPlaceholder() {
    const states = useTranslations("states");
    const locale = useLocale();
    const copy = useWidgetCopy();
    const title = copy.title(def);
    // The English sentence lowercases the noun ("Loading total eps…"); a Turkish product name
    // keeps its capitals, so the title is used as written there.
    const name = locale === "tr" ? title : title.toLowerCase();
    const Root = kpi ? "article" : "section";
    return (
      <Root className={kpi ? "kpi" : "tw"} aria-busy="true">
        <header className={kpi ? "kpi-head" : "tw-head"}>
          <h2 className={kpi ? "kpi-title" : "tw-title"}>{title}</h2>
        </header>
        <p className={kpi ? "kpi-state" : "tw-state"} role="status">
          {states("loading", { what: name })}
        </p>
      </Root>
    );
  }
  return WidgetPlaceholder;
}
