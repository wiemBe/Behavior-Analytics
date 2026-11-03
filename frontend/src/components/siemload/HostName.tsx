// A host name in a table cell: its labels stay whole, so a name never reads as two hosts.

import { Fragment } from "react";

/**
 * A host name breaks only after a ".": each DNS label is one unbreakable run, and a `<wbr>`
 * follows every dot. A name without a dot is a single run.
 */
export function HostName({ host }: { host: string }) {
  const labels = host.split(".");
  if (labels.length === 1) return <span className="sl-host sl-host-name sl-label">{host}</span>;
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
