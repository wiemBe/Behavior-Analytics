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
      screen.getByRole("heading", { name: "Configuration changes", level: 1 }),
    ).toBeInTheDocument();
    expect(screen.getByText("Configuration changes are not collected.")).toBeInTheDocument();
    expect(
      screen.getByText(/no collector or API fills it, so this page has nothing to show\./),
    ).toBeInTheDocument();
  });

  it("names no data and offers no action", () => {
    render(<ConfigChangesPage />);
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("promises nothing and names no phase", () => {
    const { container } = render(<ConfigChangesPage />);
    expect(container).not.toHaveTextContent(/phase|planned|arrive|scaffold/i);
  });
});
