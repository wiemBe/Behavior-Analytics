import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import DetailError from "./[id]/error";
import DetailLoading from "./[id]/loading";
import ListError from "./error";
import ListLoading from "./loading";

describe("property-watch route boundaries", () => {
  it.each([
    ["list", <ListLoading key="l" />, "Loading property watches…"],
    ["detail", <DetailLoading key="d" />, "Loading the property watch…"],
  ])("designs the %s loading state", (_name, view, text) => {
    render(view);
    expect(screen.getByRole("status")).toHaveTextContent(text);
  });

  it.each([
    ["list", ListError],
    ["detail", DetailError],
  ])("designs the %s error state with retry and no raw error", (_name, Component) => {
    const reset = vi.fn();
    render(<Component error={Object.assign(new Error("secret"), { digest: "safe-ref" })} reset={reset} />);
    expect(screen.getByRole("alert")).toHaveTextContent("safe-ref");
    expect(screen.queryByText("secret")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
