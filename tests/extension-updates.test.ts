import { expect, it, vi } from "vitest"

import { isVersionUpgrade, showUpdateNotice } from "../lib/extension-updates"

it.each([
  ["0.1.0", "0.2.0", true],
  ["1.9", "1.10", true],
  ["2", "2.0.0", false],
  ["2.1", "2.0", false],
  [undefined, "2", false],
  ["broken", "2", false],
  ["1", "70000", false]
])("compares browser versions %s → %s", (before, after, expected) => {
  expect(isVersionUpgrade(before, after as string)).toBe(expected)
})
it("opens once for a real upgrade, not install/reload/browser update", async () => {
  let recorded: string | undefined
  const effects = {
    read: async () => recorded,
    save: async (v: string) => {
      recorded = v
    },
    open: vi.fn().mockResolvedValue(undefined)
  }
  for (const details of [
    { reason: "install" },
    { reason: "chrome_update", previousVersion: "0.1.0" },
    { reason: "update", previousVersion: "0.2.0" }
  ]) {
    expect(await showUpdateNotice(details, "0.2.0", effects)).toBe(false)
  }
  const upgrade = { reason: "update", previousVersion: "0.1.0" }
  expect(await showUpdateNotice(upgrade, "0.2.0", effects)).toBe(true)
  expect(await showUpdateNotice(upgrade, "0.2.0", effects)).toBe(false)
  expect(effects.open).toHaveBeenCalledOnce()
  expect(recorded).toBe("0.2.0")
})
it("does not record an update page that could not open", async () => {
  const effects = {
    read: async () => undefined,
    save: vi.fn(),
    open: vi.fn().mockRejectedValue(new Error("Unavailable"))
  }
  await expect(
    showUpdateNotice({ reason: "update", previousVersion: "1" }, "2", effects)
  ).rejects.toThrow("Unavailable")
  expect(effects.save).not.toHaveBeenCalled()
})
