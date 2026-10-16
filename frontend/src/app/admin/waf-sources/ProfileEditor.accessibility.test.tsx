import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import { HISTORY, SCHEMA, catalog, profile } from "@/components/fieldmapping/fixtures";

import { ProfileEditor } from "./ProfileEditor";

it("closes the change review with Escape and returns focus to its opener", async () => {
  const user = userEvent.setup();
  render(
    <ProfileEditor
      schema={SCHEMA}
      source={{
        id: "ls-1",
        name: "WAF edge - eu-west",
        qradarId: 212,
        typeName: "Generic WAF DSM",
        instanceId: "inst-1",
      }}
      profile={profile()}
      catalog={catalog()}
      history={HISTORY}
      siblings={[]}
      prefill={null}
    />,
  );
  const field = screen.getByRole("combobox", { name: /url path property/i });
  await user.clear(field);
  await user.type(field, "request_uri");
  const review = screen.getByRole("button", { name: "Review and save" });
  await user.click(review);
  expect(screen.getByRole("region", { name: "Confirm the change" })).toHaveFocus();

  await user.keyboard("{Escape}");

  expect(screen.queryByRole("region", { name: "Confirm the change" })).toBeNull();
  expect(review).toHaveFocus();
});
