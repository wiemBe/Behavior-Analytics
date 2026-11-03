"use client";

// Asks before the page is left with unsaved work. Two ways out are covered: closing, reloading or
// typing another address (the browser's own prompt, through `beforeunload`), and following a link
// to another page of the application, which the browser never reports as leaving (a confirm of
// our own, on the click, before the router sees it). The back button is not intercepted.

import { useEffect } from "react";

export function useLeaveGuard(active: boolean, message: string): void {
  useEffect(() => {
    if (!active) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      // Some browsers still need a return value to show the prompt.
      event.returnValue = "";
    };
    const onClick = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const target = event.target instanceof Element ? event.target.closest("a[href]") : null;
      if (!target) return;
      const anchor = target as HTMLAnchorElement;
      if (anchor.target && anchor.target !== "_self") return;
      let url: URL;
      try {
        url = new URL(anchor.href, window.location.href);
      } catch {
        return;
      }
      if (url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname && url.search === window.location.search) return;
      if (!window.confirm(message)) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    // Capture phase: the router's own link handler must not run when the administrator stays.
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, [active, message]);
}
