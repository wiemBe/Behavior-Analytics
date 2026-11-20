import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { featuresApi } from "@/lib/api";

import FeaturesError from "./error";
import Loading from "./loading";
import Page from "./page";

describe("Features route", () => {
  it("has exactly one h1, a Guide link, and no description line", async () => {
    vi.spyOn(featuresApi, "get").mockReturnValue(new Promise(() => {}));
    render(await Page());
    const h1s = screen.getAllByRole("heading", { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent("Features");
    expect(screen.getByRole("link", { name: /guide/i })).toHaveAttribute("href", "/guide/features");
    expect(document.querySelector(".page-desc")).toBeNull();
  });

  it("keeps its heading in the loading boundary", () => {
    render(<Loading />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Features");
    expect(screen.getByRole("status")).toHaveTextContent("Loading the feature switches");
  });

  it("explains a render failure without the exception, and offers to retry", () => {
    const reset = vi.fn();
    render(
      <FeaturesError
        error={Object.assign(new Error("secret stack"), { digest: "ref-5" })}
        reset={reset}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Features could not be displayed.");
    expect(alert).toHaveTextContent("Reference: ref-5.");
    expect(alert).not.toHaveTextContent("secret stack");
    screen.getByRole("button", { name: "Try again" }).click();
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
