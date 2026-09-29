import { backendOrigin } from "./security"

/** Invoke from the sign-in click, before awaiting any other operation. */
export async function requestBackendAccess(origin: string): Promise<void> {
  const validOrigin = backendOrigin(origin)
  if (!validOrigin.startsWith("https:")) return
  const granted = await chrome.permissions.request({
    origins: [`${validOrigin}/*`]
  })
  if (!granted)
    throw new Error(
      "Allow Threadly to connect to its server, then sign in again."
    )
}
