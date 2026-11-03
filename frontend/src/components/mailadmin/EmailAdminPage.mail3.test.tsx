// MAIL-3: the preview at laptop widths, the browser's Back button, and the recipient draft.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EmailAdminPage } from "./EmailAdminPage";
import { placeholders, plainList, preview, recipientList, settings, template } from "./fixtures";
import { isRecipientDirty } from "./recipientDraft";

const api = vi.hoisted(() => ({
  settings: vi.fn(),
  placeholders: vi.fn(),
  templates: vi.fn(),
  template: vi.fn(),
  saveTemplate: vi.fn(),
  resetTemplate: vi.fn(),
  preview: vi.fn(),
  sendTest: vi.fn(),
  recipients: vi.fn(),
  addRecipient: vi.fn(),
  saveRecipient: vi.fn(),
  removeRecipient: vi.fn(),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  mailAdminApi: api,
}));

const TEMPLATE_SENTENCE = "A template has unsaved changes. Leave this page and lose them?";
const RECIPIENT_SENTENCE = "A recipient has not been saved. Leave this page and lose it?";
const BOTH_SENTENCE =
  "A template has unsaved changes and a recipient has not been saved. Leave this page and lose both?";

// jsdom does not lay out. The stage's width and the frame's layout width are what the scale is
// computed from, so the tests state them.
let stageWidth = 640;
const observers: Array<() => void> = [];

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  api.settings.mockResolvedValue(settings());
  api.placeholders.mockResolvedValue(placeholders());
  api.templates.mockResolvedValue(plainList());
  api.template.mockImplementation(async (key: string) => template(key));
  api.preview.mockResolvedValue(preview());
  api.recipients.mockResolvedValue(recipientList());
  observers.length = 0;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(private readonly callback: () => void) {
        observers.push(() => this.callback());
      }
      observe() {}
      disconnect() {}
    },
  );
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (this: HTMLElement) {
    return this.classList.contains("mail-stage") ? stageWidth : 0;
  });
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(function (this: HTMLElement) {
    return this.classList.contains("mail-frame") ? 640 : 0;
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function open() {
  render(<EmailAdminPage />);
  await screen.findByLabelText("Subject");
  await screen.findByTitle("E-mail preview");
}

function edit(field: HTMLElement, value: string) {
  fireEvent.change(field, { target: { value } });
}

const stage = () => screen.getByTitle("E-mail preview").parentElement as HTMLElement;
const onSentinel = () =>
  (window.history.state as { mailLeaveGuard?: boolean } | null)?.mailLeaveGuard === true;

/** The history as the analyst has it: a page before this one, then this one. */
function arriveFrom(previous: string) {
  window.history.pushState(null, "", previous);
  window.history.pushState(null, "", "/admin/email");
}

async function toRecipientTab() {
  await userEvent.click(screen.getByRole("tab", { name: /^Recipients/ }));
}

describe("the preview at laptop widths", () => {
  it("scales the whole 600 px message to a 1024 px column and says so", async () => {
    stageWidth = 448;
    await open();
    expect(stage().style.getPropertyValue("--mail-scale")).toBe("0.7");
    expect(screen.getByRole("status", { name: "" })).toBeDefined();
    expect(screen.getByText("Shown at 70 % of its size, to fit the column.")).toBeInTheDocument();
    // Scaled, not reflowed: the frame carries no width of its own, the stylesheet keeps the layout
    // width and the scale does the rest.
    expect(screen.getByTitle("E-mail preview")).not.toHaveAttribute("width");
    expect(screen.getByTitle("E-mail preview").getAttribute("style")).toBeNull();
  });

  it("scales to a 1280 px column", async () => {
    stageWidth = 560;
    await open();
    expect(stage().style.getPropertyValue("--mail-scale")).toBe("0.875");
    expect(screen.getByText("Shown at 87 % of its size, to fit the column.")).toBeInTheDocument();
  });

  it("follows the column when it is resized", async () => {
    stageWidth = 560;
    await open();
    stageWidth = 480;
    observers.forEach((notify) => notify());
    await waitFor(() => expect(stage().style.getPropertyValue("--mail-scale")).toBe("0.75"));
    expect(screen.getByText("Shown at 75 % of its size, to fit the column.")).toBeInTheDocument();
  });

  it("shows it at actual size and back, the scrolling inside the preview only", async () => {
    stageWidth = 448;
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Show at actual size" }));
    expect(stage().style.getPropertyValue("--mail-scale")).toBe("1");
    expect(stage()).toHaveClass("mail-stage-actual");
    expect(stage()).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("region", { name: "E-mail preview, scrollable" })).toBe(stage());
    expect(screen.getByText(/Shown at actual size \(100 %\)/)).toBeInTheDocument();
    expect(screen.getByTitle("E-mail preview")).toHaveAttribute("sandbox", "");
    await userEvent.click(screen.getByRole("button", { name: "Fit to the column" }));
    expect(stage().style.getPropertyValue("--mail-scale")).toBe("0.7");
    expect(stage()).not.toHaveClass("mail-stage-actual");
  });

  it("is unchanged at 1400 px and wider: full size, no scale text, no control", async () => {
    stageWidth = 640;
    await open();
    expect(stage().style.getPropertyValue("--mail-scale")).toBe("1");
    expect(screen.queryByText(/Shown at/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show at actual size" })).not.toBeInTheDocument();
  });

  it("shows an unmeasured frame at full size, never at a guessed scale", async () => {
    stageWidth = 0;
    await open();
    expect(stage().style.getPropertyValue("--mail-scale")).toBe("1");
    expect(screen.queryByText(/Shown at/)).not.toBeInTheDocument();
  });

  it("keeps the frame sandboxed exactly as before", async () => {
    stageWidth = 448;
    await open();
    const frame = screen.getByTitle("E-mail preview");
    expect(frame).toHaveAttribute("sandbox", "");
    expect(frame).toHaveAttribute("referrerpolicy", "no-referrer");
  });
});

describe("the stylesheet that lays it out (jsdom does not lay out)", () => {
  const css = readFileSync(resolve(__dirname, "mailadmin.css"), "utf8");
  const rule = (selector: string) => {
    const found = new RegExp(`\\n${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`).exec(css);
    expect(found, selector).not.toBeNull();
    return found![1];
  };

  it("puts the preview beside the editor from 1024 px", () => {
    expect(css).toMatch(/@media \(min-width: 1024px\)\s*\{\s*\.mail-work\s*\{[^}]*grid-template-columns: minmax\(0, 1fr\) minmax\(0, 1fr\)/);
  });
  it("scales the frame from its layout width and clips it to the stage, so the page never scrolls", () => {
    expect(rule(".mail-frame")).toMatch(/width: var\(--mail-frame-w\)/);
    expect(rule(".mail-frame")).toMatch(/transform: scale\(var\(--mail-scale\)\)/);
    expect(rule(".mail-stage")).toMatch(/overflow: hidden/);
    expect(rule(".mail-stage-actual")).toMatch(/overflow-x: auto/);
  });
  it("holds no literal colour or size of its own for the scale", () => {
    expect(rule(".mail-frame")).not.toMatch(/#[0-9a-f]{3,6}/i);
    expect(rule(".mail-frame")).not.toMatch(/max-width/);
  });
});

describe("the browser's Back button", () => {
  it("asks once the template is dirty; a stay keeps the drafts and the URL; a leave goes back", async () => {
    arriveFrom("/prev");
    await open();
    edit(screen.getByLabelText("Subject"), "Changed");
    expect(onSentinel()).toBe(true);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    window.history.back();
    await waitFor(() => expect(confirm).toHaveBeenCalledWith(TEMPLATE_SENTENCE));
    await waitFor(() => expect(onSentinel()).toBe(true));
    expect(window.location.pathname).toBe("/admin/email");
    expect(screen.getByLabelText("Subject")).toHaveValue("Changed");
    expect(confirm).toHaveBeenCalledTimes(1);

    // A second Back is asked again, and this time the analyst leaves.
    confirm.mockReturnValue(true);
    window.history.back();
    await waitFor(() => expect(window.location.pathname).toBe("/prev"));
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it("adds no history entry while the page is clean, and asks nothing on Back", async () => {
    arriveFrom("/prev");
    const before = window.history.length;
    await open();
    await toRecipientTab();
    await userEvent.click(screen.getByRole("tab", { name: /^Templates/ }));
    expect(window.history.length).toBe(before);
    expect(onSentinel()).toBe(false);
    const confirm = vi.spyOn(window, "confirm");
    window.history.back();
    await waitFor(() => expect(window.location.pathname).toBe("/prev"));
    expect(confirm).not.toHaveBeenCalled();
  });

  it("takes the sentinel off again when the draft is discarded: Back goes to the previous page", async () => {
    arriveFrom("/prev");
    await open();
    edit(screen.getByLabelText("Subject"), "Changed");
    expect(onSentinel()).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    await waitFor(() => expect(onSentinel()).toBe(false));
    expect(window.location.pathname).toBe("/admin/email");
    const confirm = vi.spyOn(window, "confirm");
    window.history.back();
    await waitFor(() => expect(window.location.pathname).toBe("/prev"));
    expect(confirm).not.toHaveBeenCalled();
  });

  it("takes the sentinel off again after a save: Back goes to the previous page", async () => {
    arriveFrom("/prev");
    api.saveTemplate.mockImplementation(async (key: string, body: { subject: string; html_body: string }) =>
      template(key, { customised: true, subject: body.subject, html_body: body.html_body }),
    );
    await open();
    edit(screen.getByLabelText("Subject"), "Saved subject");
    await userEvent.click(screen.getByRole("button", { name: "Save template" }));
    await screen.findByText("Template saved.");
    await waitFor(() => expect(onSentinel()).toBe(false));
    expect(window.location.pathname).toBe("/admin/email");
    const confirm = vi.spyOn(window, "confirm");
    window.history.back();
    await waitFor(() => expect(window.location.pathname).toBe("/prev"));
    expect(confirm).not.toHaveBeenCalled();
  });

  it("leaves no sentinel behind when a guarded link is confirmed", async () => {
    arriveFrom("/prev");
    await open();
    edit(screen.getByLabelText("Subject"), "Changed");
    const link = document.createElement("a");
    link.href = "/alerts";
    document.body.appendChild(link);
    const followed: boolean[] = [];
    const behind = (event: Event) => {
      followed.push(event.defaultPrevented);
      event.preventDefault();
    };
    document.addEventListener("click", behind);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    try {
      fireEvent.click(link);
      // The link is followed once the sentinel is off the history.
      await waitFor(() => expect(followed).toEqual([false]));
      expect(onSentinel()).toBe(false);
      expect(window.location.pathname).toBe("/admin/email");
      window.history.back();
      await waitFor(() => expect(window.location.pathname).toBe("/prev"));
    } finally {
      document.removeEventListener("click", behind);
      link.remove();
    }
  });
});

describe("a half-typed recipient", () => {
  async function typeRecipient(address = "half@exa") {
    await toRecipientTab();
    await userEvent.click(screen.getByRole("button", { name: "Add recipient" }));
    await userEvent.type(screen.getByLabelText("E-mail address"), address);
  }

  it("survives a visit to the Templates tab and is flagged on the tab", async () => {
    await open();
    await typeRecipient();
    expect(screen.getByRole("tab", { name: "Recipients (not saved)" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /^Templates/ }));
    expect(screen.queryByLabelText("E-mail address")).not.toBeInTheDocument();
    await toRecipientTab();
    expect(screen.getByLabelText("E-mail address")).toHaveValue("half@exa");
  });

  it("is counted by the leave guard with its own sentence, on close, on a link and on Back", async () => {
    arriveFrom("/prev");
    await open();
    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);
    await typeRecipient();
    const dirty = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);

    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    window.history.back();
    await waitFor(() => expect(confirm).toHaveBeenCalledWith(RECIPIENT_SENTENCE));
    await waitFor(() => expect(onSentinel()).toBe(true));
    expect(screen.getByLabelText("E-mail address")).toHaveValue("half@exa");

    const link = document.createElement("a");
    link.href = "/alerts";
    document.body.appendChild(link);
    try {
      fireEvent.click(link);
      expect(confirm).toHaveBeenLastCalledWith(RECIPIENT_SENTENCE);
    } finally {
      link.remove();
    }
  });

  it("asks one sentence naming both when a template and a recipient are unsaved", async () => {
    arriveFrom("/prev");
    await open();
    edit(screen.getByLabelText("Subject"), "Changed");
    await typeRecipient();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    window.history.back();
    await waitFor(() => expect(confirm).toHaveBeenCalledWith(BOTH_SENTENCE));
    expect(confirm).toHaveBeenCalledTimes(1);
    // The template alone is back to its own sentence once the recipient is cancelled.
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(onSentinel()).toBe(true));
    window.history.back();
    await waitFor(() => expect(confirm).toHaveBeenLastCalledWith(TEMPLATE_SENTENCE));
  });

  it("does not count a form that was opened and not touched, and is clean after Cancel", async () => {
    arriveFrom("/prev");
    await open();
    await toRecipientTab();
    await userEvent.click(screen.getByRole("button", { name: "Add recipient" }));
    expect(onSentinel()).toBe(false);
    expect(screen.getByRole("tab", { name: "Recipients" })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("E-mail address"), "x");
    expect(onSentinel()).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(onSentinel()).toBe(false));
  });

  it("is clean once it is saved", async () => {
    arriveFrom("/prev");
    api.addRecipient.mockResolvedValue({});
    await open();
    await typeRecipient("new@example.org");
    expect(onSentinel()).toBe(true);
    await userEvent.click(screen.getByRole("form", { name: "Add a recipient" }).querySelector("button[type=submit]")!);
    await waitFor(() => expect(api.addRecipient).toHaveBeenCalled());
    await waitFor(() => expect(onSentinel()).toBe(false));
  });
});

describe("isRecipientDirty", () => {
  const base = { id: null, address: "", minSeverity: "LOW" as const, everyType: true, types: [], enabled: true };
  it("compares the form with what it was opened with", () => {
    expect(isRecipientDirty(null)).toBe(false);
    expect(isRecipientDirty({ form: base, base })).toBe(false);
    expect(isRecipientDirty({ form: { ...base, address: "a" }, base })).toBe(true);
    expect(isRecipientDirty({ form: { ...base, minSeverity: "HIGH" }, base })).toBe(true);
    expect(isRecipientDirty({ form: { ...base, everyType: false }, base })).toBe(true);
    expect(isRecipientDirty({ form: { ...base, enabled: false }, base })).toBe(true);
    expect(isRecipientDirty({ form: { ...base, types: ["x"] }, base })).toBe(true);
    const edited = { ...base, id: "r-1", address: "a@b.c", types: ["x", "y"] };
    expect(isRecipientDirty({ form: { ...edited, types: ["y", "x"] }, base: edited })).toBe(false);
  });
});
