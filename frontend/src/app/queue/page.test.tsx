import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import QueuePage from "./page";
import QueueLoading from "./loading";
import QueueError from "./error";

vi.mock("@/components/queue/QueueView", () => ({
  QueueView: ({ query }: { query: { scope: string; kinds: string[]; compare: string } }) => (
    <p data-testid="view">{`${query.scope}|${query.kinds.join(",")}|${query.compare}`}</p>
  ),
}));

describe("the queue route", () => {
  it("names the page and hands the parsed address to the view", async () => {
    render(await QueuePage({ searchParams: Promise.resolve({ scope: "mine", kind: ["alert", "bogus"], compare: "8h" }) }));
    expect(screen.getByRole("heading", { level: 1, name: "Queue" })).toBeInTheDocument();
    expect(screen.getByTestId("view")).toHaveTextContent("mine|alert|8h");
    expect(screen.getByRole("button", { name: /copy a link to this view/i })).toBeInTheDocument();
  });

  it("keeps the heading while loading", () => {
    render(<QueueLoading />);
    expect(screen.getByRole("heading", { level: 1, name: "Queue" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading the queue");
  });

  it("says what failed and offers a retry", () => {
    const reset = vi.fn();
    render(<QueueError error={Object.assign(new Error("x"), { digest: "d1" })} reset={reset} />);
    expect(screen.getByRole("alert")).toHaveTextContent("The queue could not be displayed");
    screen.getByRole("button", { name: "Try again" }).click();
    expect(reset).toHaveBeenCalled();
  });
});
