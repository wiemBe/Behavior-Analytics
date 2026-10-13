// A sender (syslog hostname) in monospace.
//
// It may wrap after a `.` or a `-`, never in the middle of a word; only a single
// unbroken run longer than the cell breaks anywhere (the CSS fallback). The full
// value is always the tooltip.

import { Fragment } from "react";

import { breakParts } from "./catchall";

export function SenderId({ value }: { value: string }) {
  const parts = breakParts(value);
  return (
    <code className="sender-id" title={value}>
      {parts.map((part, i) => (
        <Fragment key={i}>
          {part}
          {i < parts.length - 1 && <wbr />}
        </Fragment>
      ))}
    </code>
  );
}
