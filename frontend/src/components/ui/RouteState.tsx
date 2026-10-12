// The two states every data route can be in besides "has data".
//
// Both are plain text. A spinner or a skeleton would be decoration: the page
// is waiting on a server round trip, and what the analyst needs is to know
// that, and what to do if it does not end.

/** Shown by a route's `loading.tsx` while its server data is fetched. */
export function LoadingState({ what }: { what: string }) {
  return (
    <p className="notice" role="status" aria-live="polite">
      Loading {what}…
    </p>
  );
}

/**
 * Shown by a route's `error.tsx` when rendering threw.
 *
 * The error itself is deliberately not rendered: an unexpected exception can
 * carry a stack trace or an upstream URL, and this text goes into the DOM. The
 * digest is Next's opaque reference to the server log, so it is safe to show
 * and lets an analyst quote it to whoever reads that log.
 */
export function ErrorState({
  what,
  digest,
  onRetry,
}: {
  what: string;
  digest?: string;
  onRetry: () => void;
}) {
  return (
    <div className="notice" role="alert">
      <p>
        <strong>{what} could not be displayed.</strong>
      </p>
      <p>
        The page failed while rendering. Nothing on it was changed. Try again; if
        it keeps failing, check that the backend is reachable.
        {digest ? ` Reference: ${digest}.` : ""}
      </p>
      <p>
        <button type="button" className="action secondary" onClick={onRetry}>
          Try again
        </button>
      </p>
    </div>
  );
}
