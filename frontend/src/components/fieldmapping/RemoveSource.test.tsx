import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, fleetApi, ipsApi, type ProfileClassOut } from "@/lib/api";

import { RemoveSource, type RemoveFamily } from "./RemoveSource";

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
});

afterEach(() => vi.restoreAllMocks());

const override = (family: RemoveFamily): ProfileClassOut => ({
  source_class: family,
  basis: "OVERRIDE",
  override_class: family,
});
const byRule = (family: RemoveFamily): ProfileClassOut => ({
  source_class: family,
  basis: "RULE",
  override_class: null,
});

function setup(family: RemoveFamily, klass: ProfileClassOut | "fail") {
  const remove =
    family === "WAF"
      ? vi.spyOn(api, "removeWafProfile").mockResolvedValue(undefined)
      : vi.spyOn(ipsApi, "removeProfile").mockResolvedValue(undefined);
  const read =
    family === "WAF" ? vi.spyOn(api, "wafSourceClass") : vi.spyOn(ipsApi, "sourceClass");
  if (klass === "fail") read.mockRejectedValue(new ApiError(500, "boom"));
  else read.mockResolvedValue(klass);
  const clear = vi.spyOn(fleetApi, "setOverride").mockResolvedValue({} as never);
  const onRemoved = vi.fn();
  render(
    <RemoveSource
      family={family}
      logSourceId="ls-9"
      name="f51waf_upt"
      label="Remove"
      onRemoved={onRemoved}
    />,
  );
  return { remove, clear, onRemoved, read };
}

async function open() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Remove" }));
  return user;
}

describe.each<RemoveFamily>(["WAF", "IPS"])("RemoveSource (%s)", (family) => {
  it("names the source and what stays, and does nothing until confirmed", async () => {
    const { remove } = setup(family, override(family));
    await open();
    expect(
      screen.getByRole("heading", { name: `Remove f51waf_upt from ${family}?` }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Collection stops. Collected history stays until retention."),
    ).toBeInTheDocument();
    expect(remove).not.toHaveBeenCalled();
  });

  it("cancel closes without removing", async () => {
    const { remove, onRemoved } = setup(family, override(family));
    const user = await open();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(remove).not.toHaveBeenCalled();
    expect(onRemoved).not.toHaveBeenCalled();
    expect(screen.queryByRole("heading", { name: /Remove f51waf_upt/ })).not.toBeInTheDocument();
  });

  it("removes and clears the class by default when the class is an override", async () => {
    const { remove, clear, onRemoved } = setup(family, override(family));
    const user = await open();
    const box = await screen.findByRole("checkbox", { name: `Also clear the ${family} class` });
    expect(box).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Remove source" }));
    await waitFor(() => expect(onRemoved).toHaveBeenCalledWith(null));
    expect(remove).toHaveBeenCalledWith("ls-9");
    expect(clear).toHaveBeenCalledWith("ls-9", null);
  });

  it("keeps the class when the option is unchecked", async () => {
    const { remove, clear, onRemoved } = setup(family, override(family));
    const user = await open();
    await user.click(await screen.findByRole("checkbox", { name: `Also clear the ${family} class` }));
    await user.click(screen.getByRole("button", { name: "Remove source" }));
    await waitFor(() => expect(onRemoved).toHaveBeenCalled());
    expect(remove).toHaveBeenCalled();
    expect(clear).not.toHaveBeenCalled();
  });

  it("names a rule-set class, links the rules page and offers no clear option", async () => {
    const { clear, onRemoved } = setup(family, byRule(family));
    const user = await open();
    expect(await screen.findByText(`The ${family} class comes from a rule.`)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Classification rules" })).toHaveAttribute(
      "href",
      "/admin/classification-rules",
    );
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove source" }));
    await waitFor(() => expect(onRemoved).toHaveBeenCalledWith(null));
    expect(clear).not.toHaveBeenCalled();
  });

  it("still removes when the class could not be read, and says so", async () => {
    const { remove, clear, onRemoved } = setup(family, "fail");
    const user = await open();
    expect(await screen.findByText("The class could not be read.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove source" }));
    await waitFor(() => expect(onRemoved).toHaveBeenCalled());
    expect(remove).toHaveBeenCalled();
    expect(clear).not.toHaveBeenCalled();
  });

  it("a failed remove keeps the dialog open with one line and leaves the class alone", async () => {
    const { remove, clear, onRemoved } = setup(family, override(family));
    remove.mockRejectedValue(new ApiError(500, "boom"));
    const user = await open();
    await screen.findByRole("checkbox");
    await user.click(screen.getByRole("button", { name: "Remove source" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      `Could not remove the ${family} source. Try again.`,
    );
    expect(onRemoved).not.toHaveBeenCalled();
    expect(clear).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Remove source" })).toBeEnabled();
  });

  it("a forbidden remove names the permission", async () => {
    const { remove, onRemoved } = setup(family, override(family));
    remove.mockRejectedValue(new ApiError(403, "no"));
    const user = await open();
    await user.click(screen.getByRole("button", { name: "Remove source" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      `Not permitted: removing needs admin:${family.toLowerCase()}.`,
    );
    expect(onRemoved).not.toHaveBeenCalled();
  });

  it("an already-removed source (404) counts as removed", async () => {
    const { remove, onRemoved } = setup(family, byRule(family));
    remove.mockRejectedValue(new ApiError(404, "gone"));
    const user = await open();
    await user.click(screen.getByRole("button", { name: "Remove source" }));
    await waitFor(() => expect(onRemoved).toHaveBeenCalledWith(null));
  });

  it("reports a class that stayed when the profile is gone but the clear failed", async () => {
    const { clear, onRemoved } = setup(family, override(family));
    clear.mockRejectedValue(new ApiError(403, "no"));
    const user = await open();
    await screen.findByRole("checkbox");
    await user.click(screen.getByRole("button", { name: "Remove source" }));
    await waitFor(() =>
      expect(onRemoved).toHaveBeenCalledWith(
        `f51waf_upt was removed, but clearing its ${family} class needs log source write.`,
      ),
    );
  });

  it("reports a class that stayed on a non-permission failure", async () => {
    const { clear, onRemoved } = setup(family, override(family));
    clear.mockRejectedValue(new ApiError(500, "boom"));
    const user = await open();
    await screen.findByRole("checkbox");
    await user.click(screen.getByRole("button", { name: "Remove source" }));
    await waitFor(() =>
      expect(onRemoved).toHaveBeenCalledWith(
        `f51waf_upt was removed, but its ${family} class stays.`,
      ),
    );
  });
});
