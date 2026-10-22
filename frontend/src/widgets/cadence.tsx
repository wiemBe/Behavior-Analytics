"use client";

// The collection cadence per search family, read once for a whole Dashboard view (UI-7.2).
//
// A widget that knows how far its data reaches asks its frame to judge staleness (`deriveStale`,
// UR.5.4), and the frame needs the cadence of the family that collected the data. The provider
// reads `GET /admin/search-cadence` through `useApiResource`, so every widget of the view, and the
// Scheduled searches widget, share the one request. A role without `read:search_cadence`, or a
// failed read, leaves the cadence unknown: `deriveStale` then applies its 2 h default. A family's
// cadence is never guessed here.

import { createContext, useContext, type ReactNode } from "react";

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads, type SearchCadenceItem, type SearchFamily } from "@/lib/api";

const CadenceContext = createContext<readonly SearchCadenceItem[] | null>(null);

export function CadenceProvider({ children }: { children: ReactNode }) {
  const read = homeReads.searchCadence();
  const state = useApiResource(read.key, read.fetch);
  return (
    <CadenceContext.Provider value={state.status === "ok" ? state.data : null}>
      {children}
    </CadenceContext.Provider>
  );
}

export interface FamilyTiming {
  /** Null when the read was refused, failed or has no row for the family. */
  cadenceSeconds: number | null;
  /** The family's bucket width; null when unknown or the family has no buckets. */
  bucketSeconds: number | null;
}

const UNKNOWN: FamilyTiming = { cadenceSeconds: null, bucketSeconds: null };

/** The family's cadence and bucket width, or unknown when the view has no cadence read. */
export function useFamilyTiming(family: SearchFamily | null | undefined): FamilyTiming {
  const items = useContext(CadenceContext);
  if (!family || !items) return UNKNOWN;
  const item = items.find((i) => i.family === family);
  return item
    ? { cadenceSeconds: item.cadence_seconds, bucketSeconds: item.bucket_seconds }
    : UNKNOWN;
}
