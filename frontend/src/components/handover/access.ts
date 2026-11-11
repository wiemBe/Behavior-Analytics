"use client";

// Whether the signed-in account may write a handover. Reads the session the shell holds; while
// it is still being read, or could not be read, the answer is "unknown", never "yes".

import { holds } from "@/components/auth/access";
import { useSession } from "@/components/auth/session";

export const WRITE_PERMISSION = "anomaly:triage";

export type WriteAccess = "yes" | "no" | "unknown";

export function useWriteAccess(): WriteAccess {
  const session = useSession();
  if (session.status === "loading") return "unknown";
  if (session.status !== "ready" || !session.me) return "unknown";
  const granted = [...session.me.permissions, ...session.me.effective_permissions];
  return holds(granted, WRITE_PERMISSION) ? "yes" : "no";
}
