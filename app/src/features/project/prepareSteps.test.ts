import { expect, it } from "vitest";
import { prepareSteps } from "./prepareSteps";

it("splits overall progress into steps", () => {
  expect(prepareSteps(0.525).map((s) => Math.round(s.value * 100))).toEqual([100, 100, 50, 0]);
  expect(prepareSteps(1).every((s) => s.value === 1)).toBe(true);
});
