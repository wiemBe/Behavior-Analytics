"use client";

import { useEffect, useState } from "react";

/** The browser's clock, read after mount: null on the server and the first render, so the markup
 *  matches. A relative age is drawn only once it is known and never replaces the UTC time. */
export function useNow(): number | null {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    setNow(Date.now());
  }, []);
  return now;
}
