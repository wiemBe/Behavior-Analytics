"use client";

// One line with the API's build version, read from the open `GET /version` route. A failed read
// says so; it never falls back to a remembered or hard-coded number.

import { useEffect, useState } from "react";

import { shellApi } from "@/lib/api";

export function ApiVersionLine({ className }: { className?: string }) {
  const [state, setState] = useState<{ version: string } | "failed" | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    shellApi
      .version(ctl.signal)
      .then((v) => {
        if (!ctl.signal.aborted) setState({ version: v.version });
      })
      .catch(() => {
        if (!ctl.signal.aborted) setState("failed");
      });
    return () => ctl.abort();
  }, []);

  if (state === null) return null;
  return (
    <p className={className}>
      {state === "failed" ? "API version could not be read" : `API version ${state.version}`}
    </p>
  );
}
