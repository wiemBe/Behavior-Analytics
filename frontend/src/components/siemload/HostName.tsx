// A host name in a table cell: its labels stay whole, so a name never reads as two hosts.

import { Fragment } from "react";

/**
 * A name up to this many characters is one unbreakable run. An auto-layout table sizes a column
 * to its widest unbreakable run, so a `<wbr>` after the first dot of "qradarce2.local" made the
 * Host column as wide as "qradarce2." and the name wrapped while the row had room (FIX-26).
 */
export const HOST_RUN_MAX = 20;
// UX-6 tried 24: with a 24-character FQDN in every table the Health table scrolls inside its own
// box at 1024x768 (811 of 780 px), so the bound stays 20 and a 21-24 character name breaks after
// a dot.

/**
 * A host name breaks only after a ".": each DNS label is one unbreakable run, and a `<wbr>`
 * follows every dot. A name without a dot, or one of up to HOST_RUN_MAX characters, is a single
 * run: the table gives it the width it needs, and wraps a longer FQDN only after a dot.
 */
export function HostName({ host }: { host: string }) {
  const labels = host.split(".");
  if (labels.length === 1 || host.length <= HOST_RUN_MAX) return <span className="sl-host sl-host-name sl-label">{host}</span>;
  return (
    <span className="sl-host sl-host-name">
      {labels.map((label, i) => (
        <Fragment key={i}>
          <span className="sl-label">{i < labels.length - 1 ? `${label}.` : label}</span>
          {i < labels.length - 1 ? <wbr /> : null}
        </Fragment>
      ))}
    </span>
  );
}
