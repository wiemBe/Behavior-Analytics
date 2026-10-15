import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ConfigChangesPage from "./page";

// States this route renders, from `page.tsx`: one state only. The route is a
// scaffolded placeholder with no data read, so there is no loading, empty or error
// state and no query parameter. The test pins the copy a restyle must not silently
// change: the heading, the phase, and the explanation that the model and API are
// Phase-3 work.

describe("configuration changes scaffold", () => {
  it("renders the heading, the phase and the scaffold explanation", () => {
    render(<ConfigChangesPage />);
    expect(
      screen.getByRole("heading", { name: "Configuration Changes", level: 2 }),
    ).toBeInTheDocument();
    expect(screen.getByText("Planned for Phase 3.")).toBeInTheDocument();
    expect(
      screen.getByText(/This section is scaffolded\. Its data model and API exist \(or arrive\) in Phase 3;/),
    ).toBeInTheDocument();
    expect(screen.getByText(/the view is implemented in that phase per the development order\./)).toBeInTheDocument();
  });

  it("names no data and offers no action", () => {
    render(<ConfigChangesPage />);
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
