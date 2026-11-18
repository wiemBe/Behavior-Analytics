"use client";

import { useEffect, useRef, useState } from "react";
import type { PropertyCatalogOut } from "@/lib/api";

/** Poll a queued refresh for at most one minute; abandon reads after unmount. */
export function usePropertyCatalog(initial: PropertyCatalogOut | null, read: () => Promise<PropertyCatalogOut>) {
  const [catalog, setCatalog] = useState(initial);
  const readRef = useRef(read);
  readRef.current = read;
  useEffect(() => {
    if (catalog?.catalog_status !== "REFRESHING") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let attempts = 0;
    async function poll() {
      try {
        const next = await readRef.current();
        if (cancelled) return;
        setCatalog(next);
        if (next.catalog_status !== "REFRESHING") return;
      } catch { /* Keep the last known list and stop after the bounded attempts. */ }
      if (!cancelled && ++attempts < 30) timer = setTimeout(poll, 2000);
    }
    timer = setTimeout(poll, 2000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [catalog?.catalog_status]);
  return [catalog, setCatalog] as const;
}
