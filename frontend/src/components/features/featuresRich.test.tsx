import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { canReadFeatures, featuresRich, withFeaturesLink } from "./featuresRich";

describe("canReadFeatures", () => {
  it.each([
    [["read:features"], true],
    [["read:*"], true],
    [["admin:*"], true],
    [["admin:features"], false],
    [["read:property_watches"], false],
    [[], false],
  ])("%j is %s", (permissions, expected) => {
    expect(canReadFeatures({ permissions })).toBe(expected);
  });

  it("fails closed when the account is unread", () => {
    expect(canReadFeatures(null)).toBe(false);
    expect(canReadFeatures(undefined)).toBe(false);
  });
});

describe("the notice helpers", () => {
  it("featuresRich links only when linked", () => {
    const { rerender } = render(<p>{featuresRich(true).features("Administration → Features")}</p>);
    expect(screen.getByRole("link")).toHaveAttribute("href", "/admin/features");
    rerender(<p>{featuresRich(false).features("Administration → Features")}</p>);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("Administration → Features")).toBeInTheDocument();
  });

  it("withFeaturesLink keeps the words and only adds the link", () => {
    const text = "Off. Turn it on under Administration → Features.";
    const { container, rerender } = render(<p>{withFeaturesLink(text, true)}</p>);
    expect(container.textContent).toBe(text);
    expect(screen.getAllByRole("link")).toHaveLength(1);
    // The arrow is inside the anchor: one link, one focus stop, the whole name.
    expect(screen.getByRole("link")).toHaveTextContent(/^Administration → Features$/);
    expect(screen.getByRole("link")).toHaveClass("features-link");
    rerender(<p>{withFeaturesLink(text, false)}</p>);
    expect(screen.queryByRole("link")).toBeNull();
    expect(container.textContent).toBe(text);
  });

  it("withFeaturesLink leaves a sentence without the page name alone", () => {
    const { container } = render(<p>{withFeaturesLink("RULE_MATCH_COLLECTION_ENABLED is off", true)}</p>);
    expect(container.textContent).toBe("RULE_MATCH_COLLECTION_ENABLED is off");
    expect(screen.queryByRole("link")).toBeNull();
  });
});
