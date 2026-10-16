import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import DetailError from "./[id]/error";
import DetailLoading from "./[id]/loading";
import ListError from "./error";
import ListLoading from "./loading";
import NewError from "./new/error";
import NewLoading from "./new/loading";

describe("custom-rule route boundaries", () => {
  it.each([
    ["list", <ListLoading key="list" />, "Loading custom rules…"],
    ["new", <NewLoading key="new" />, "Loading the rule editor…"],
    ["detail", <DetailLoading key="detail" />, "Loading the custom rule…"],
  ])("designs the %s loading state", (_name, view, text) => {
    render(view);
    expect(screen.getByRole("status")).toHaveTextContent(text);
  });

  it.each([
    ["list", ListError],
    ["new", NewError],
    ["detail", DetailError],
  ])("designs the %s error state with retry", (_name, Component) => {
    const reset = vi.fn();
    render(<Component error={Object.assign(new Error("secret"), { digest: "safe-ref" })} reset={reset} />);
    expect(screen.getByRole("alert")).toHaveTextContent("safe-ref");
    expect(screen.queryByText("secret")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalled();
  });
});
