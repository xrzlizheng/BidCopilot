import { describe, expect, it } from "vitest";

import { hasPermission } from "./auth-store";

describe("hasPermission", () => {
  it("supports wildcard and namespace permissions", () => {
    expect(hasPermission(["asset:*"], "asset:write")).toBe(true);
    expect(hasPermission(["platform:*"], "platform:tenant:manage")).toBe(true);
    expect(hasPermission(["*"], "anything")).toBe(true);
  });

  it("rejects missing permissions", () => {
    expect(hasPermission(["asset:read"], "asset:write")).toBe(false);
    expect(hasPermission(undefined, "opportunity:read")).toBe(false);
  });
});
