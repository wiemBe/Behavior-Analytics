"use client";

// The map's outlines: Natural Earth 1:110m admin-0 (public domain, v5.1.2), 175 countries keyed by
// ISO alpha-2, about 178 KB (56 KB gzipped). They are fetched with a dynamic `import()`, so the
// bundler puts them in a chunk of their own that only a page holding a map ever requests: the
// Dashboard bundle, and every route without a map, never carries them. A chunk beats a file in
// `public/` here because it is versioned and cache-busted with the build, needs no route, no base
// path and no permission of its own, and loads the same way in a test; the request is made once
// per page load and a failure is not cached, so the next map retries.

import { useEffect, useState } from "react";

export interface World {
  /** The GeoJSON `ECharts` registers. */
  geoJson: unknown;
  /** The ISO codes the outlines hold a polygon for. */
  codes: ReadonlySet<string>;
}

interface Collection {
  features: Array<{ id?: string; properties?: { iso_a2?: string } }>;
}

let pending: Promise<World> | null = null;

/** Loads the outlines once. A failed load is dropped, so a later call tries again. */
export function loadWorld(): Promise<World> {
  if (pending === null) {
    pending = import("./world-110m.geo.json")
      .then((module) => {
        const geoJson = module.default as unknown as Collection;
        const codes = new Set<string>();
        for (const feature of geoJson.features) {
          const code = feature.properties?.iso_a2 ?? feature.id;
          if (code) codes.add(code);
        }
        return { geoJson, codes };
      })
      .catch((error: unknown) => {
        pending = null;
        throw error;
      });
  }
  return pending;
}

/** Forgets the loaded outlines (tests). */
export function resetWorld(): void {
  pending = null;
}

export type WorldState =
  | { status: "loading" }
  | { status: "ok"; world: World }
  | { status: "error" };

/** The outlines, loading on first use. `enabled` false leaves them unloaded. */
export function useWorld(enabled = true): WorldState {
  const [state, setState] = useState<WorldState>({ status: "loading" });
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    loadWorld().then(
      (world) => live && setState({ status: "ok", world }),
      () => live && setState({ status: "error" }),
    );
    return () => {
      live = false;
    };
  }, [enabled]);
  return state;
}
