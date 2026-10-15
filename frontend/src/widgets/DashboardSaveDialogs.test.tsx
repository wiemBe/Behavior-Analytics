// The two dialogs whose body used to restate their heading (T-098): each body now says what
// each choice does.

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConflictDialog, UnsavedChangesDialog } from "./DashboardSaveDialogs";

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
});

describe("ConflictDialog", () => {
  const open = () =>
    render(
      <ConflictDialog
        dashboardName="Day shift"
        busy={false}
        onReload={vi.fn(async () => {})}
        onSaveCopy={vi.fn(async () => null)}
        onClose={vi.fn()}
      />,
    );

  it("says what Reload, Save as a copy and Keep editing each do", () => {
    open();
    const dialog = screen.getByRole("dialog", { name: "This dashboard changed since you opened it" });
    expect(dialog).toHaveTextContent("Reload discards your changes and shows the saved version.");
    expect(dialog).toHaveTextContent("Save as a copy keeps your changes under the name below.");
    expect(dialog).toHaveTextContent("Keep editing leaves both as they are.");
  });

  it("does not restate its heading", () => {
    open();
    const dialog = screen.getByRole("dialog");
    expect(dialog).not.toHaveTextContent(/saved by someone else/i);
    const body = dialog.querySelector(".dg-modal-choices")!;
    expect(body.textContent).not.toMatch(/changed since you opened it/i);
  });
});

describe("UnsavedChangesDialog", () => {
  const open = () =>
    render(<UnsavedChangesDialog dashboardName="Day shift" onDiscard={vi.fn()} onKeep={vi.fn()} />);

  it("says what Discard and Keep editing each do", () => {
    open();
    const dialog = screen.getByRole("dialog", { name: "Discard unsaved changes to Day shift?" });
    expect(dialog).toHaveTextContent("Discard drops your changes and shows the saved layout.");
    expect(dialog).toHaveTextContent("Keep editing leaves the dashboard as it is.");
  });

  it("does not restate its heading", () => {
    open();
    expect(screen.getByRole("dialog")).not.toHaveTextContent("have not been saved");
  });
});
