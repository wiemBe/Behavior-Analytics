import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { StaticSession } from "@/components/auth/session";
import { session } from "@/components/alerts/suppressionFixtures";

import HandoverError from "./error";
import HandoverLoading from "./loading";
import HandoverPage from "./page";

vi.mock("@/components/handover/HandoverView", () => ({
  HandoverView: ({ id }: { id: string | null }) => <p data-testid="view">{`view:${id ?? "latest"}`}</p>,
}));
vi.mock("@/components/handover/ComposeView", () => ({
  ComposeView: () => <p data-testid="compose">compose</p>,
}));

describe("the handover route", () => {
  it("asks the question and shows the record view, with the guide link", async () => {
    render(<StaticSession value={session(["read:alerts"])}>{await HandoverPage({ searchParams: Promise.resolve({}) })}</StaticSession>);
    expect(screen.getByRole("heading", { level: 1, name: "Handover" })).toBeInTheDocument();
    expect(screen.getByText("What did the last shift leave?")).toBeInTheDocument();
    expect(screen.getByTestId("view")).toHaveTextContent("view:latest");
    expect(screen.queryByRole("link", { name: "Write handover" })).toBeNull();
  });

  it("offers Write only with anomaly:triage", async () => {
    render(
      <StaticSession value={session(["anomaly:triage"])}>{await HandoverPage({ searchParams: Promise.resolve({}) })}</StaticSession>,
    );
    expect(screen.getByRole("link", { name: "Write handover" })).toHaveAttribute("href", "/handover?compose=1");
  });

  it("passes ?id= to the view and ?compose=1 to the form", async () => {
    const { unmount } = render(await HandoverPage({ searchParams: Promise.resolve({ id: "abc" }) }));
    expect(screen.getByTestId("view")).toHaveTextContent("view:abc");
    unmount();
    render(<StaticSession value={session(["anomaly:triage"])}>{await HandoverPage({ searchParams: Promise.resolve({ compose: "1" }) })}</StaticSession>);
    expect(screen.getByTestId("compose")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Write a handover" })).toBeInTheDocument();
    expect(screen.getByText("Freeze what is open now and leave a note for the next shift.")).toBeInTheDocument();
    expect(screen.queryByText("What did the last shift leave?")).toBeNull();
    expect(screen.queryByRole("link", { name: "Write handover" })).toBeNull();
  });

  it("keeps the heading while loading and offers a retry on failure", () => {
    const { unmount } = render(<HandoverLoading />);
    expect(screen.getByRole("heading", { level: 1, name: "Handover" })).toBeInTheDocument();
    unmount();
    const reset = vi.fn();
    render(<HandoverError error={Object.assign(new Error("x"), { digest: "d1" })} reset={reset} />);
    screen.getByRole("button", { name: "Try again" }).click();
    expect(reset).toHaveBeenCalled();
  });
});
