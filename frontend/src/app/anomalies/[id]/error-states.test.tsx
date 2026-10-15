// UI-5.2: a failed investigation read keeps the page title as the h1 and says what to do next.

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomalyDetailPage from "./page";
import { ApiError, api } from "@/lib/api";

beforeEach(() => {
  vi.restoreAllMocks();
});

async function failWith(status: number) {
  vi.spyOn(api, "anomaly").mockRejectedValue(new ApiError(status, "x"));
  render(await AnomalyDetailPage({ params: Promise.resolve({ id: "x" }) }));
}

describe("investigation read failures", () => {
  it.each([404, 403, 401, 500])("on %i keeps one h1 and a way back to the list", async (status) => {
    await failWith(status);
    expect(screen.getByRole("heading", { level: 1, name: "Anomaly investigation" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to all anomalies" })).toHaveAttribute(
      "href",
      "/anomalies",
    );
  });

  it("tells the analyst to reload when the backend cannot be reached", async () => {
    await failWith(500);
    expect(screen.getByRole("alert")).toHaveTextContent(/reload the page to try again/i);
  });

  it("names the missing grant on a 403", async () => {
    await failWith(403);
    expect(screen.getByRole("alert")).toHaveTextContent(/read:anomalies/);
  });
});
