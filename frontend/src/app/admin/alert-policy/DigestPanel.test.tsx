import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DigestConsoles, DigestPanel } from "./DigestPanel";
import { INST_A, digest, run } from "./fixtures";

function show(d = digest(), note: string | null = null) {
  return render(<DigestPanel digest={d} note={note} />);
}

const said = () => document.querySelector(".digest-say") as HTMLElement;

describe("the digest panel", () => {
  it("SUCCESS: due hour, state, date, sources, recipients and the sent time in UTC", () => {
    show();
    expect(screen.getByText("Daily at 07:00 UTC")).toBeInTheDocument();
    expect(screen.getByText("3 addresses")).toBeInTheDocument();
    expect(screen.getByText("Delivered")).toBeInTheDocument();
    expect(screen.getByText("Digest for 2026-11-06")).toBeInTheDocument();
    expect(screen.getByText("4 sources")).toBeInTheDocument();
    expect(document.body.textContent).toMatch(/2026-11-06 07:05 UTC|11-06 07:05 UTC/);
    expect(said()).toHaveTextContent("The digest listed 4 sources and went to 3 recipients.");
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("EMPTY: says nothing was waiting, not that it failed", () => {
    show(digest({ last: run({ state: "EMPTY", item_count: 0, recipient_count: 3, sent_at: null }) }));
    expect(screen.getByText("Nothing to send")).toBeInTheDocument();
    expect(said()).toHaveTextContent("No source had digest incidents waiting, so nothing was sent.");
  });

  it("NOT_CONFIGURED: counts the sources whose incidents were not e-mailed and links to the e-mail settings", () => {
    show(
      digest({
        email_configured: false,
        recipient_count: 0,
        last: run({ state: "NOT_CONFIGURED", item_count: 7, recipient_count: 0, sent_at: null }),
      }),
    );
    expect(said()).toHaveTextContent(
      "7 sources have digest incidents that are not e-mailed; e-mail is not configured.",
    );
    expect(screen.getByRole("link", { name: "Set up e-mail" })).toHaveAttribute("href", "/admin/email");
    // Not repeated as a second "not configured now" line: the run already says so.
    expect(document.body.textContent).not.toContain("E-mail is not configured now");
  });

  it("NOT_CONFIGURED with one source uses the singular", () => {
    show(digest({ last: run({ state: "NOT_CONFIGURED", item_count: 1, sent_at: null }) }));
    expect(said()).toHaveTextContent("1 source has digest incidents that are not e-mailed");
  });

  it("NO_RECIPIENTS: same idea, linked to the recipients", () => {
    show(
      digest({
        recipient_count: 0,
        last: run({ state: "NO_RECIPIENTS", item_count: 2, recipient_count: 0, sent_at: null }),
      }),
    );
    expect(said()).toHaveTextContent("2 sources had digest incidents that were not e-mailed: no recipient");
    expect(screen.getByRole("link", { name: "Manage recipients" })).toHaveAttribute(
      "href",
      "/admin/email#recipients",
    );
    expect(screen.getByText("0 addresses")).toBeInTheDocument();
  });

  it("FAILED: words the delivery code with the e-mail page's sentence and next step", () => {
    show(digest({ last: run({ state: "FAILED", error_code: "connection_refused", sent_at: null }) }));
    expect(screen.getByText("Delivery failed")).toBeInTheDocument();
    expect(said()).toHaveTextContent("The digest (4 sources) was not delivered.");
    expect(said()).not.toHaveTextContent(/incidents are recorded|incidents were recorded/);
    expect(said()).toHaveTextContent(/doctor --check-smtp/);
    expect(screen.getByRole("link", { name: "Open the e-mail settings" })).toHaveAttribute(
      "href",
      "/admin/email",
    );
    expect(document.body.textContent).not.toContain("connection_refused");
  });

  it("FAILED with a code the page does not word still says it was not delivered", () => {
    show(digest({ last: run({ state: "FAILED", error_code: "brand_new_code", sent_at: null }) }));
    expect(said()).toHaveTextContent("was not delivered. The relay reported an outcome");
  });

  it("no run yet: says so and never draws an item count", () => {
    show(digest({ last: null, instances: [] }));
    expect(said()).toHaveTextContent("No digest has run yet. The first is due at 07:00 UTC.");
    expect(screen.queryByText("Sources listed")).toBeNull();
    expect(screen.queryByText(/0 sources/)).toBeNull();
    expect(screen.getByText("Daily at 07:00 UTC")).toBeInTheDocument();
  });

  it("warns that the next digest will not go out when e-mail was configured then and is not now", () => {
    show(digest({ email_configured: false }));
    expect(said()).toHaveTextContent("E-mail is not configured now, so the next digest will not be sent.");
  });

  it("warns about an empty recipient list now after a run that had recipients", () => {
    show(digest({ recipient_count: 0 }));
    expect(said()).toHaveTextContent("No recipient is set to receive alert e-mail now");
  });

  it("one console shows no per-console table; several do, with a never-run console as such", () => {
    const one = render(<DigestConsoles digest={digest()} />);
    expect(screen.queryByRole("table")).toBeNull();
    one.unmount();
    render(
      <DigestConsoles
        digest={digest({
        instances: [
          { instance_id: INST_A, instance_name: "lab-console", last_run: run() },
          { instance_id: "22222222-2222-4222-8222-222222222222", instance_name: "prod", last_run: null },
        ],
      })}
      />,
    );
    const table = screen.getByRole("table", { name: "Last digest run for each console" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(within(rows[1]).getByText("lab-console")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Delivered")).toBeInTheDocument();
    expect(within(rows[2]).getByText("No run yet")).toBeInTheDocument();
  });

  it("an unreadable status says what failed and keeps no numbers", () => {
    show(null as never, "The digest status could not be read. Reload the page; the policy table is not affected.");
    expect(screen.getByRole("alert")).toHaveTextContent("The digest status could not be read");
    expect(screen.getByRole("alert")).toHaveTextContent("Reload the page");
    expect(screen.queryByText(/Daily at/)).toBeNull();
  });
});
