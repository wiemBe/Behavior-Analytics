import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { QRadarNotificationList } from "@/lib/api";

import { consoleLatest } from "./fixtures";
import { NotificationsTable } from "./NotificationsTable";

const list = (over: Partial<QRadarNotificationList> = {}): QRadarNotificationList => ({
  ...consoleLatest().notification_list!,
  ...over,
});

describe("NotificationsTable", () => {
  it("lists QID name, host, count, severity and last seen in UTC, one row per group", () => {
    render(<NotificationsTable console_={consoleLatest()} hours={24} />);
    expect(screen.getByRole("table", { name: "System notifications of qradar-lab" })).toBeInTheDocument();
    const row = screen.getByRole("row", { name: /Warning Message/ });
    const cells = within(row).getAllByRole("cell");
    expect(cells[0]).toHaveTextContent("qr-console");
    expect(cells[1]).toHaveTextContent("42");
    expect(cells[2]).toHaveTextContent("4");
    expect(cells[3]).toHaveTextContent("2026-11-02 10:09:12 UTC");
    for (const cell of cells.slice(1)) expect(cell).toHaveClass("num");
    expect(screen.getByRole("columnheader", { name: "Severity (0 to 10)" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Last seen (UTC)" })).toBeInTheDocument();
  });

  it("shows a dash for a severity QRadar did not give, never a 0", () => {
    render(
      <NotificationsTable
        console_={consoleLatest({
          notification_list: list({
            entries: [{ host: "h", qid_name: "Odd", events: 1, max_severity: null, last_seen: null }],
          }),
        })}
        hours={24}
      />,
    );
    const cells = within(screen.getByRole("row", { name: /Odd/ })).getAllByRole("cell");
    expect(cells[2]).toHaveTextContent("—");
    expect(cells[3]).toHaveTextContent("—");
  });

  it("counts the groups it did not list on a final line", () => {
    render(
      <NotificationsTable
        console_={consoleLatest({ notification_list: list({ remainder: { events: 1_204, groups: 31 } }) })}
        hours={24}
      />,
    );
    expect(screen.getByText("Plus 1,204 events in 31 further groups, not listed.")).toBeInTheDocument();
  });

  it("says in words that a range that was looked at held no notification", () => {
    render(
      <NotificationsTable
        console_={consoleLatest({ notification_list: list({ entries: [], remainder: null }) })}
        hours={24}
      />,
    );
    expect(
      screen.getByText("QRadar logged no system notification in the last 24 h, other than information messages."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("states what the range covers: windows, cadence and the UTC span", () => {
    render(<NotificationsTable console_={consoleLatest()} hours={24} />);
    const line = screen.getByText(/96 windows of 15 min/);
    expect(line).toHaveTextContent("2026-11-01 10:15:00 UTC");
    expect(line).toHaveTextContent("2026-11-02 10:15:00 UTC");
    expect(line).not.toHaveTextContent("Only");
  });

  it("warns that the counts can be low when the windows cover less than the range", () => {
    render(
      <NotificationsTable
        console_={consoleLatest({ notification_list: list({ windows: 40 }) })}
        hours={24}
      />,
    );
    expect(screen.getByText(/Only 10 h of the last 24 h were collected, so these counts can be low\./)).toBeInTheDocument();
  });

  it("is not collected, with the reason, when no window in the range was measured", () => {
    render(
      <NotificationsTable
        console_={consoleLatest({
          notification_list: null,
          notifications: { state: "NOT_COLLECTED", reason_code: "no_window", reason: "No window has been stored yet." },
        })}
        hours={24}
      />,
    );
    expect(screen.getByText("Not collected")).toBeInTheDocument();
    expect(screen.getByText("No window has been stored yet.")).toBeInTheDocument();
    expect(screen.queryByText(/no system notification/)).not.toBeInTheDocument();
  });

  it("is failed, in the error tone, when the search failed and no earlier window holds a list", () => {
    render(
      <NotificationsTable
        console_={consoleLatest({
          notification_list: null,
          notifications: { state: "FAILED", reason_code: "search_failed", reason: "The Ariel search failed." },
        })}
        hours={24}
      />,
    );
    expect(screen.getByText("Failed")).toHaveClass("crit");
    expect(screen.getByText("The Ariel search failed.")).toBeInTheDocument();
  });

  it("says the newest window failed while listing the windows before it", () => {
    render(
      <NotificationsTable
        console_={consoleLatest({
          notifications: { state: "FAILED", reason_code: "search_failed", reason: "The Ariel search failed." },
        })}
        hours={24}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("The Ariel search failed.");
    expect(screen.getByRole("status")).toHaveTextContent("The list covers the windows before it.");
    expect(screen.getByRole("row", { name: /Warning Message/ })).toBeInTheDocument();
  });

  it("says a stale window is stale above its list", () => {
    render(
      <NotificationsTable
        console_={consoleLatest({
          notifications: { state: "STALE", reason_code: "stale", reason: "The newest window is older than the cadence allows." },
        })}
        hours={24}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("older than the cadence allows");
  });
});

describe("NotificationsTable host names", () => {
  const withHosts = (hosts: string[]) =>
    render(
      <NotificationsTable
        console_={consoleLatest({
          notification_list: list({
            entries: hosts.map((host) => ({
              host,
              qid_name: `Name of ${host}`,
              events: 1,
              max_severity: 1,
              last_seen: null,
            })),
            remainder: null,
          }),
        })}
        hours={24}
      />,
    );

  it("renders a short host name as one unbreakable run with no break opportunity", () => {
    const { container } = withHosts(["qradarce2"]);
    const host = container.querySelector(".sl-host-name") as HTMLElement;
    expect(host).toHaveTextContent("qradarce2");
    expect(host).toHaveClass("sl-label");
    expect(host.children).toHaveLength(0);
  });

  it("offers a break only after the dots of an FQDN, each label staying whole", () => {
    const fqdn = "ep-01.eventprocessor.dc1.corp.example.com";
    const { container } = withHosts([fqdn]);
    const host = container.querySelector(".sl-host-name") as HTMLElement;
    expect(host).toHaveTextContent(fqdn);
    const labels = [...host.querySelectorAll(".sl-label")].map((el) => el.textContent);
    expect(labels).toEqual(["ep-01.", "eventprocessor.", "dc1.", "corp.", "example.", "com"]);
    expect(host.querySelectorAll("wbr")).toHaveLength(5);
    for (const wbr of host.querySelectorAll("wbr")) {
      expect(wbr.previousElementSibling?.textContent).toMatch(/\.$/);
    }
  });

  it("keeps the number columns right-aligned and the QID name as the row header", () => {
    const { container } = withHosts(["qradarce2"]);
    expect(container.querySelector("table")).toHaveClass("sl-notices-table");
    const row = screen.getByRole("row", { name: /Name of qradarce2/ });
    expect(within(row).getByRole("rowheader")).toHaveTextContent("Name of qradarce2");
    for (const cell of within(row).getAllByRole("cell").slice(1)) expect(cell).toHaveClass("num");
  });
});
