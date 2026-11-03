import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { mailAdminApi } from "@/lib/api";

import EmailAdminError from "./error";
import Loading from "./loading";
import Page from "./page";

describe("Alert e-mail route", () => {
  it("has exactly one h1, a guide link, and states that times are UTC", async () => {
    vi.spyOn(mailAdminApi, "settings").mockReturnValue(new Promise(() => {}));
    vi.spyOn(mailAdminApi, "templates").mockReturnValue(new Promise(() => {}));
    vi.spyOn(mailAdminApi, "placeholders").mockReturnValue(new Promise(() => {}));
    vi.spyOn(mailAdminApi, "recipients").mockReturnValue(new Promise(() => {}));
    vi.spyOn(mailAdminApi, "template").mockReturnValue(new Promise(() => {}));
    render(await Page());
    const h1s = screen.getAllByRole("heading", { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent("Alert e-mail");
    expect(screen.getByText(/Times are UTC\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /guide/i })).toHaveAttribute("href", "/guide/alert-email");
    expect(document.body.textContent).not.toMatch(/real-time|\blive\b|!/i);
  });

  it("keeps its heading in the loading boundary", () => {
    render(<Loading />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Alert e-mail");
    expect(screen.getByRole("status")).toHaveTextContent("Loading the alert e-mail settings");
  });

  it("explains a render failure without the exception, and offers to retry", () => {
    const reset = vi.fn();
    render(
      <EmailAdminError
        error={Object.assign(new Error("secret stack"), { digest: "ref-9" })}
        reset={reset}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("alert e-mail could not be displayed.");
    expect(alert).toHaveTextContent("Nothing on it was changed.");
    expect(alert).toHaveTextContent("Reference: ref-9.");
    expect(alert).not.toHaveTextContent("secret stack");
    screen.getByRole("button", { name: "Try again" }).click();
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
