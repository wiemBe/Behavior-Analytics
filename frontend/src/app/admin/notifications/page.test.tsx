import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { notificationRoutingApi } from "@/lib/api";

import NotificationRoutingError from "./error";
import Loading from "./loading";
import Page from "./page";

describe("Notification routing route", () => {
  it("has exactly one h1 and states that times are UTC", async () => {
    vi.spyOn(notificationRoutingApi, "rules").mockReturnValue(new Promise(() => {}));
    vi.spyOn(notificationRoutingApi, "destinations").mockReturnValue(new Promise(() => {}));
    render(await Page());
    const h1s = screen.getAllByRole("heading", { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent("Notification routing");
    expect(screen.getByText(/Which alerts leave the site, and where the webhooks go\./)).toBeInTheDocument();
    expect(screen.getByText(/Times are UTC\./)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/real-time|\blive\b|!/i);
  });

  it("keeps its heading in the loading boundary", () => {
    render(<Loading />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Notification routing");
    expect(screen.getByRole("status")).toHaveTextContent("Loading the notification routing settings");
  });

  it("explains a render failure without the exception, and offers to retry", () => {
    const reset = vi.fn();
    render(
      <NotificationRoutingError
        error={Object.assign(new Error("secret stack"), { digest: "ref-9" })}
        reset={reset}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("notification routing could not be displayed.");
    expect(alert).toHaveTextContent("Reference: ref-9.");
    expect(alert).not.toHaveTextContent("secret stack");
    screen.getByRole("button", { name: "Try again" }).click();
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
