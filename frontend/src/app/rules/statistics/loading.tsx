import Link from "next/link";

import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";
import { DAY_CHOICES } from "@/components/rules/ruleStatistics";

export default function Loading() {
  const daySelector = (
    <nav className="range-selector stats-days" aria-label="Measurement days">
      <span className="muted">Window</span>
      {DAY_CHOICES.map((choice) => (
        <Link
          key={choice}
          href={`/rules/statistics?days=${choice}`}
          className={`range-option${choice === 7 ? " range-option-active" : ""}`}
          aria-current={choice === 7 ? "page" : undefined}
        >
          {choice} {choice === 1 ? "day" : "days"}
        </Link>
      ))}
    </nav>
  );

  return (
    <BoundaryLoading
      title="Rule statistics"
      what="rule statistics"
      description="Inventory composition, measured matching load, proven silence and QRadar evaluation benchmarks."
      actions={daySelector}
    />
  );
}
