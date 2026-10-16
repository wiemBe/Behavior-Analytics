import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import { HISTORY, IPS_SCHEMA, catalog, profile } from "./fixtures";
import { IpsProfileEditor } from "./IpsProfileEditor";

it("closes the change review with Escape and returns focus to its opener", async () => {
  const user = userEvent.setup();
  render(
    <IpsProfileEditor
      schema={IPS_SCHEMA}
      source={{
        id: "ls-413",
        name: "CiscoFirepowerThreatDefense @ ftd-01",
        qradarId: 413,
        typeName: "Cisco Firepower Threat Defense",
      }}
      profile={profile()}
      catalog={catalog()}
      history={HISTORY}
    />,
  );
  const group = screen.getByRole("group", { name: /signature reference kind/i });
  await user.click(within(group).getByRole("radio", { name: "Custom property" }));
  await user.type(screen.getByRole("combobox", { name: /signature property/i }), "Threat_ID");
  const review = screen.getByRole("button", { name: "Review and save" });
  await user.click(review);
  expect(screen.getByRole("region", { name: "Confirm the change" })).toHaveFocus();

  await user.keyboard("{Escape}");

  expect(screen.queryByRole("region", { name: "Confirm the change" })).toBeNull();
  expect(review).toHaveFocus();
});
