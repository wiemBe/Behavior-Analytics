import { describe, expect, it } from "vitest";

import { errorTexts } from "./reasons";
import { ApiError } from "@/lib/api";

describe("errorTexts", () => {
  it("returns a string detail as sent", () => {
    expect(errorTexts(new ApiError(401, "Invalid username or password", "Invalid username or password"))).toEqual([
      "Invalid username or password",
    ]);
  });

  it("lists every reason in a 422 list, including pydantic messages", () => {
    expect(
      errorTexts(
        new ApiError(422, "Unprocessable", [
          "too short",
          { msg: "matches the username" },
          { msg: "   " },
          "",
        ]),
      ),
    ).toEqual(["too short", "matches the username"]);
  });

  it("falls back when the error is not from the API", () => {
    expect(errorTexts(new Error("nope"))).toEqual([
      "The request could not be sent. Check your connection and try again.",
    ]);
  });
});
