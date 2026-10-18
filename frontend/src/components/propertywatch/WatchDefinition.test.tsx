import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { catalog, scope, watch } from "./fixtures";
import { WatchDefinition } from "./WatchDefinition";

describe("the Definition tab cadence", () => {
  it.each([
    [300, "Every 5 min"],
    [3600, "Every 1 h"],
    [8 * 3600, "Every 8 h"],
  ])("states a %s-second family cadence", (cadence_seconds, expected) => {
    render(
      <WatchDefinition
        watch={watch({ cadence_seconds })}
        catalog={catalog()}
        scope={scope()}
      />,
    );
    const cadence = screen.getByText(expected, { exact: false });
    expect(cadence).toHaveTextContent("PROPERTY_WATCH family's setting");
    expect(cadence).toHaveTextContent("buckets are five minutes wide");
    expect(screen.getByRole("link", { name: "Search cadence" })).toHaveAttribute(
      "href",
      "/admin/search-cadence",
    );
  });
});
