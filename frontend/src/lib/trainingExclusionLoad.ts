// The first read of a source's training exclusions, made by the server pages (SOC-13d).
// A failed read is its own state, never an empty list; a read the caller may not make names the
// permission. The Retry on the source page makes the same read from the browser.

import { ApiError, trainingExclusionApi, type TrainingExclusion } from "@/lib/api";
import { permissionOf } from "@/lib/trainingExclusion";

export const READ_PERMISSION = "read:anomalies";

export type ExclusionLoad =
  | { kind: "ok"; items: TrainingExclusion[] }
  | { kind: "error" }
  | { kind: "forbidden"; permission: string };

/** The subject whose exclusions are listed (SOC-13f): what the list endpoint filters by. */
export interface ExclusionSubject {
  kind: string;
  id: string;
  /** Omitted for a rule, whose storm and silence scopes are listed together. */
  scope?: string;
}

export async function loadExclusions(sourceId: string): Promise<ExclusionLoad> {
  return read(() => trainingExclusionApi.forSource(sourceId));
}

/** The same first read for any family's subject. */
export async function loadExclusionsFor(subject: ExclusionSubject): Promise<ExclusionLoad> {
  return read(() => trainingExclusionApi.forSubject(subject.kind, subject.id, subject.scope));
}

async function read(call: () => Promise<{ items: TrainingExclusion[] }>): Promise<ExclusionLoad> {
  try {
    return { kind: "ok", items: (await call()).items };
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      return { kind: "forbidden", permission: permissionOf(err) ?? READ_PERMISSION };
    }
    return { kind: "error" };
  }
}
