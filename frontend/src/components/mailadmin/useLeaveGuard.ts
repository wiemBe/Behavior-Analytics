"use client";

// Asks before the page is left with unsaved work. Three ways out are covered: closing, reloading or
// typing another address (the browser's own prompt, through `beforeunload`); following a link to
// another page of the application, which the browser never reports as leaving (a confirm of our
// own, on the click, before the router sees it); and the browser's Back button.
//
// Back has no hook in the App Router, so while the page is dirty one sentinel entry with the same
// URL sits on top of the history. Back lands on the page's own entry, which is a `popstate` we can
// answer: a stay pushes the sentinel again (the URL never changed, the drafts were never touched),
// a leave steps back once more. The sentinel is taken off again when the page becomes clean and
// before a confirmed link is followed, so Back after a save, a discard or a guarded link goes to the
// previous page and not to this one. Forward cannot reach a dirty page: pushing the sentinel drops
// every forward entry. Left unguarded: a programmatic navigation (not a link) leaves the sentinel
// behind, and so does a confirmed reload, where the page is new and clean anyway.

import { useEffect, useRef } from "react";

export function useLeaveGuard(active: boolean, message: string): void {
  const messageRef = useRef(message);
  useEffect(() => {
    messageRef.current = message;
  });
  // Declared before the guard's own effect: on unmount this cleanup runs first, and tells the
  // guard's cleanup that the page is going away, not becoming clean.
  const unmounting = useRef(false);
  useEffect(() => {
    unmounting.current = false;
    return () => {
      unmounting.current = true;
    };
  }, []);

  useEffect(() => {
    if (!active) return;
    let phase: "guard" | "leaving" | "done" = "guard";
    let onTop = false;
    let resume: (() => void) | null = null;

    const push = () => {
      const state: unknown = window.history.state;
      const base = state !== null && typeof state === "object" ? state : {};
      window.history.pushState({ ...base, mailLeaveGuard: true }, "", window.location.href);
      onTop = true;
    };

    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      // Some browsers still need a return value to show the prompt.
      event.returnValue = "";
    };
    const onPop = () => {
      if (phase === "leaving") {
        // The click passes the guard it was confirmed at, then the guard stands again: if the
        // router does not leave (a refused navigation), the next click is asked as before. The
        // sentinel is not pushed again, so a navigation that does happen finds a clean history.
        phase = "done";
        resume?.();
        phase = "guard";
        return;
      }
      if (phase !== "guard") return;
      onTop = false;
      if (window.confirm(messageRef.current)) {
        phase = "done";
        window.history.back();
      } else {
        push();
      }
    };
    const onClick = (event: MouseEvent) => {
      if (phase !== "guard") return;
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
      if (!window.confirm(messageRef.current)) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      if (!onTop) return;
      // Confirmed: take the sentinel off first, then follow the link once the history has settled.
      event.preventDefault();
      event.stopPropagation();
      phase = "leaving";
      onTop = false;
      resume = () => anchor.click();
      window.history.back();
    };

    push();
    window.addEventListener("beforeunload", beforeUnload);
    window.addEventListener("popstate", onPop);
    // Capture phase: the router's own link handler must not run when the administrator stays.
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      window.removeEventListener("popstate", onPop);
      document.removeEventListener("click", onClick, true);
      if (phase === "guard" && onTop && !unmounting.current) {
        // The page became clean (saved or discarded): the sentinel goes.
        phase = "done";
        window.history.back();
      }
    };
  }, [active]);
}
