import type { ReactNode } from "react";

import "./watches.css";

// The wrapper is the hook for the header rule in watches.css: it scopes that rule to these routes.
export default function WatchesLayout({ children }: { children: ReactNode }) {
  return <div className="pw-route">{children}</div>;
}
