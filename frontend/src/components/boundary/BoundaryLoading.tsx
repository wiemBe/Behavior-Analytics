// Route boundaries keep the page's real heading in place, then delegate the state itself to the
// normative plain-text components. Titles are deliberately static: a boundary cannot know a
// source or finding name until the page's read succeeds.

import type { ReactNode } from "react";

import { PageHeader } from "@/components/ui/PageHeader";
import { ErrorState, LoadingState } from "@/components/ui/RouteState";

import "./boundary.css";

export function BoundaryLoading({
  title,
  what,
  description,
  actions,
  offset = "none",
}: {
  title: string;
  what: string;
  description?: ReactNode;
  actions?: ReactNode;
  offset?: "none" | "small" | "medium" | "large";
}) {
  return (
    <>
      <PageHeader title={title} description={description} actions={actions} />
      <div className={`boundary-state boundary-state-${offset}`}>
        <LoadingState what={what} />
      </div>
    </>
  );
}

export function BoundaryError({
  title,
  what,
  digest,
  onRetry,
}: {
  title: string;
  what: string;
  digest?: string;
  onRetry: () => void;
}) {
  return (
    <>
      <PageHeader title={title} />
      <ErrorState what={what} digest={digest} onRetry={onRetry} />
    </>
  );
}
