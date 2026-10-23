"use client";

// A server-rendered page cannot call a hook: it renders this to tell the top bar how far its
// data reaches (`useReportDataAge`). It draws nothing. `iso` null means the page's responses
// carry no such time, and the top bar then shows no "Data to" line.

import { useReportDataAge } from "./AutoRefresh";

export function ReportDataAge({ iso }: { iso: string | null }) {
  useReportDataAge(iso);
  return null;
}
