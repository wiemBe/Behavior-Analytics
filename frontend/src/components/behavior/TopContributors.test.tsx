import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TopContributors } from "./TopContributors";

describe("TopContributors without contributors", () => {
  it("refers a failed collection to the banner instead of restating it", () => {
    render(<TopContributors packaged={null} anomalyType="VOLUME_SPIKE" evidenceStatus="FAILED" />);
    expect(screen.getByText(/evidence collection failed \(see the notice below\)/i)).toBeInTheDocument();
    expect(screen.queryByText(/lifecycle below are unaffected/i)).toBeNull();
  });

  it("still says nothing has been examined when evidence was never requested", () => {
    render(
      <TopContributors packaged={null} anomalyType="VOLUME_SPIKE" evidenceStatus="NOT_REQUESTED" />,
    );
    expect(screen.getByText(/nothing has been ruled out/i)).toBeInTheDocument();
  });
});
