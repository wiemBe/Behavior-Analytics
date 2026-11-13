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

export async function loadExclusions(sourceId: string): Promise<ExclusionLoad> {
  try {
    return { kind: "ok", items: (await trainingExclusionApi.forSource(sourceId)).items };
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      return { kind: "forbidden", permission: permissionOf(err) ?? READ_PERMISSION };
    }
    return { kind: "error" };
  }
}
