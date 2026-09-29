import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { requestBackendAccess } from "../lib/backend-access"
import SidePanel from "../sidepanel"

const publicOrigin = "https://threadly-api.example.test"

beforeEach(() => {
  vi.mocked(chrome.permissions.request).mockReset()
  vi.mocked(chrome.runtime.sendMessage).mockReset()
})

describe("backend host access", () => {
  it("requests only the configured HTTPS origin", async () => {
    vi.mocked(chrome.permissions.request).mockImplementation(async () => true)
    await requestBackendAccess(publicOrigin)
    expect(chrome.permissions.request).toHaveBeenCalledWith({
      origins: [`${publicOrigin}/*`]
    })
  })

  it("requires a grant before sign-in and explains a denial", async () => {
    vi.mocked(chrome.permissions.request).mockImplementation(async () => false)
    await expect(requestBackendAccess(publicOrigin)).rejects.toThrow(
      "Allow Threadly to connect to its server"
    )
  })

  it("keeps localhost development working without an HTTPS permission", async () => {
    await requestBackendAccess("http://127.0.0.1:8000")
    expect(chrome.permissions.request).not.toHaveBeenCalled()
  })

  it("does not begin OAuth when public server access is denied", async () => {
    const calls: string[] = []
    vi.mocked(chrome.runtime.sendMessage).mockImplementation(async (m: any) => {
      calls.push(m.type)
      if (m.type === "STATUS")
        return {
          ok: true,
          data: { user: null, origin: publicOrigin, redirectUri: "" }
        } as any
      throw new Error(`Unexpected ${m.type}`)
    })
    vi.mocked(chrome.permissions.request).mockImplementation(async () => {
      calls.push("PERMISSION")
      return false
    })
    render(<SidePanel />)
    fireEvent.click(
      await screen.findByRole("button", { name: "Sign in with Google" })
    )
    await screen.findByRole("alert")
    expect(screen.getByRole("alert").textContent).toContain(
      "Allow Threadly to connect to its server"
    )
    expect(calls).toEqual(["STATUS", "PERMISSION"])
    expect(chrome.permissions.request).toHaveBeenCalledWith({
      origins: [`${publicOrigin}/*`]
    })
  })

  it("grants public server access before the OAuth request", async () => {
    const calls: string[] = []
    vi.mocked(chrome.runtime.sendMessage).mockImplementation(async (m: any) => {
      calls.push(m.type)
      return {
        ok: true,
        data:
          m.type === "STATUS"
            ? { user: null, origin: publicOrigin, redirectUri: "" }
            : null
      } as any
    })
    vi.mocked(chrome.permissions.request).mockImplementation(async () => {
      calls.push("PERMISSION")
      return true
    })
    render(<SidePanel />)
    fireEvent.click(
      await screen.findByRole("button", { name: "Sign in with Google" })
    )
    await waitFor(() => expect(calls).toContain("LOGIN"))
    expect(calls.slice(0, 3)).toEqual(["STATUS", "PERMISSION", "LOGIN"])
  })
})
