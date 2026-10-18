import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { aql, HOSTILE, iso } from "./fixtures";
import { WatchAqlSection, type AqlRead } from "./WatchAql";

const ok = (over = {}): AqlRead => ({ kind: "ok", aql: aql(over) });

describe("the generated query text", () => {
  it("labels the text as generated and names the window it was generated for", () => {
    render(<WatchAqlSection read={ok()} />);
    expect(
      screen.getByText("Generated query text. The collector adds the time range and the page limit when it runs."),
    ).toBeInTheDocument();
    expect(screen.getByText(/Generated for/)).toHaveTextContent("2026-10-18 07:00:00 UTC");
    expect(screen.getByText(/Generated for/)).toHaveTextContent("2026-10-18 08:00:00 UTC");
  });

  it("keeps every query in a collapsed, read-only panel with no input", () => {
    const { container } = render(<WatchAqlSection read={ok()} />);
    const panels = [...container.querySelectorAll("details.code-panel")];
    expect(panels).toHaveLength(3); // two current, one last bucket
    for (const p of panels) expect(p).not.toHaveAttribute("open");
    expect(container.querySelector("textarea, input")).toBeNull();
    expect(screen.getAllByText("Read-only").length).toBe(3);
  });

  it("renders a hostile pinned value in the text as text, never as markup", () => {
    const { container } = render(
      <WatchAqlSection
        read={ok({ current: [{ search: "PINNED", text: `... WHERE "username" = '${HOSTILE}'` }] })}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("code")?.textContent).toContain(HOSTILE);
  });

  it("shows the reason instead of the text when the current query is unavailable", () => {
    const { container } = render(
      <WatchAqlSection
        read={ok({ current: [], current_unavailable: "the scope does not resolve: the group is not in the synced inventory" })}
      />,
    );
    expect(screen.getByText("The query text for the current definition could not be generated.")).toBeInTheDocument();
    expect(screen.getByText(/group is not in the synced inventory/)).toBeInTheDocument();
    expect(screen.queryByText(/Generated for/)).toBeNull();
    // The last bucket's text is still offered.
    expect(container.querySelectorAll("details.code-panel")).toHaveLength(1);
  });

  it("gives the last bucket's UTC time and watch version with its texts", () => {
    render(<WatchAqlSection read={ok()} />);
    const line = screen.getByText(/^Bucket/);
    expect(line).toHaveTextContent("2026-10-18 08:55:00 UTC");
    expect(line).toHaveTextContent("watch version 3");
    expect(line.querySelector("time")).toHaveAttribute("datetime", iso(11));
  });

  it("says the watch has changed since the last bucket was collected", () => {
    render(
      <WatchAqlSection
        read={ok({ watch_version: 4, last_bucket: { ...aql().last_bucket!, watch_version: 3 } })}
      />,
    );
    expect(screen.getByText(/^Bucket/)).toHaveTextContent("the watch has since changed to version 4");
  });

  it("says no bucket has been collected yet, rather than showing an empty panel", () => {
    const { container } = render(<WatchAqlSection read={ok({ last_bucket: null })} />);
    expect(screen.getByText(/No bucket collected yet/)).toBeInTheDocument();
    expect(container.querySelectorAll("details.code-panel")).toHaveLength(2);
  });

  it("says a bucket with no recorded text has none", () => {
    render(<WatchAqlSection read={ok({ last_bucket: { ...aql().last_bucket!, queries: [] } })} />);
    expect(screen.getByText(/No query text was recorded for that bucket/)).toBeInTheDocument();
  });

  it("names what failed and what to do when the read failed", () => {
    render(<WatchAqlSection read={{ kind: "error", status: 500 }} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("could not be loaded");
    expect(alert).toHaveTextContent("reload the page");
  });

  it("names the permission when the read was refused", () => {
    render(<WatchAqlSection read={{ kind: "error", status: 403 }} />);
    expect(within(screen.getByRole("alert")).getByText(/forbidden/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("read:property_watches");
  });

  it("treats a read that never happened as a failure, not as empty", () => {
    render(<WatchAqlSection read={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
  });
});
