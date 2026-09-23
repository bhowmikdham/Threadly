import { cleanup } from "@testing-library/react"
import { afterEach, vi } from "vitest"

afterEach(() => cleanup())
Object.defineProperty(navigator, "clipboard", {
  configurable: true,
  value: { writeText: vi.fn().mockResolvedValue(undefined) }
})
Element.prototype.scrollIntoView = vi.fn()
vi.stubGlobal("chrome", {
  runtime: { sendMessage: vi.fn() },
  tabs: { query: vi.fn(), sendMessage: vi.fn() },
  storage: {
    local: { get: vi.fn().mockResolvedValue({}), set: vi.fn() },
    onChanged: { addListener: vi.fn(), removeListener: vi.fn() }
  },
  permissions: { request: vi.fn().mockResolvedValue(true) }
})
