"use client";

// The signed-in account, read from `/me`. The cookie is the credential. This
// context holds no token. A later `/me` wins over an earlier one, so a slow
// first read cannot overwrite a sign-in that already finished.

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { fetchSession, logout } from "./client";
import { ApiError, type CallerInfo } from "@/lib/api";

export interface SessionValue {
  status: "loading" | "anonymous" | "error" | "ready";
  me: CallerInfo | null;
  error: string | null;
  reload: () => Promise<void>;
  /** Null when the session ended. A string is the reason it did not. */
  signOut: () => Promise<string | null>;
}

const ABSENT: SessionValue = {
  status: "loading",
  me: null,
  error: null,
  reload: async () => {},
  signOut: async () => null,
};

const SessionContext = createContext<SessionValue | null>(null);

export function useSession(): SessionValue {
  return useContext(SessionContext) ?? ABSENT;
}

/** A fixed session for tests and for a frame that already holds one. */
export function StaticSession({ value, children }: { value: SessionValue; children: ReactNode }) {
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const requestId = useRef(0);
  const [status, setStatus] = useState<SessionValue["status"]>("loading");
  const [me, setMe] = useState<CallerInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    const id = requestId.current + 1;
    requestId.current = id;
    try {
      const next = await fetchSession(signal);
      if (id !== requestId.current || signal?.aborted) return;
      setMe(next);
      setError(null);
      setStatus("ready");
    } catch (err) {
      if (id !== requestId.current || signal?.aborted) return;
      setMe(null);
      if (err instanceof ApiError && err.status === 401) {
        setError(null);
        setStatus("anonymous");
        return;
      }
      setError(err instanceof ApiError ? err.message : "The session could not be read.");
      setStatus("error");
      throw err;
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal).catch(() => {
      /* `load` already recorded the error */
    });
    return () => controller.abort();
  }, [load]);

  const reload = useCallback(async () => {
    await load();
  }, [load]);

  const signOut = useCallback(async () => {
    try {
      await logout();
    } catch (err) {
      if (!(err instanceof ApiError) || err.status !== 401) {
        const message = err instanceof ApiError ? err.message : "Sign out could not be completed.";
        setError(message);
        return message;
      }
    }
    requestId.current += 1;
    setMe(null);
    setError(null);
    setStatus("anonymous");
    router.replace("/login");
    return null;
  }, [router]);

  const value: SessionValue = { status, me, error, reload, signOut };
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
