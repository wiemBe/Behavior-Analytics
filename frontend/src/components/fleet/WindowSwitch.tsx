// The fleet window, as links: the window belongs in the URL so a view can be
// shared, and links need no script. `aria-current` marks the active window.

import Link from "next/link";

import { FLEET_WINDOWS } from "@/lib/fleet";
import type { FleetWindow } from "@/lib/api";

export function WindowSwitch({ active }: { active: FleetWindow }) {
  return (
    <nav className="range-selector" aria-label="Fleet window">
      <span className="muted">Window</span>
      {FLEET_WINDOWS.map((w) => (
        <Link
          key={w.value}
          href={`/fleet?window=${w.value}`}
          className={`range-option${w.value === active ? " range-option-active" : ""}`}
          aria-current={w.value === active ? "page" : undefined}
        >
          {w.label}
        </Link>
      ))}
    </nav>
  );
}
