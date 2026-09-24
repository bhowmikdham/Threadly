export const DEFAULT_BACKEND = "http://127.0.0.1:8000"
export function backendOrigin(value: string): string {
  const url = new URL(value)
  if (
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    !["", "/"].includes(url.pathname)
  )
    throw new Error(
      "Enter only the server origin, without a path or credentials."
    )
  if (
    url.protocol !== "https:" &&
    !(
      url.protocol === "http:" &&
      ["127.0.0.1", "localhost"].includes(url.hostname)
    )
  )
    throw new Error("Use HTTPS, or a localhost tunnel for development.")
  return url.origin
}
export function allowedPath(path: string) {
  if (
    !/^\/(auth|assistant|calendar|threads|commitments)(\/|\?|$)/.test(path) &&
    path !== "/readyz"
  )
    return false
  return (
    !/[\\#\r\n]/.test(path) &&
    !path.includes("..") &&
    !/%(?:2f|5c|2e)/i.test(path)
  )
}
export function allowedRequest(path: string, method: string) {
  if (!allowedPath(path)) return false
  if (["GET", "POST", "PUT"].includes(method)) return true
  return (
    method === "DELETE" &&
    /^\/assistant\/conversations\/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(
      path
    )
  )
}
export function trustedSender(
  sender: { id?: string; url?: string; tab?: unknown },
  id: string
) {
  return (
    sender.id === id &&
    !!sender.url &&
    sender.url.startsWith(`chrome-extension://${id}/`)
  )
}
export function callbackCode(
  callback: string,
  redirect: string,
  state: string
) {
  const url = new URL(callback),
    expected = new URL(redirect)
  if (
    url.origin !== expected.origin ||
    url.pathname !== expected.pathname ||
    url.hash ||
    url.searchParams.getAll("state").length !== 1 ||
    url.searchParams.get("state") !== state
  )
    throw new Error(
      "Sign-in response did not match this login. Please sign in again."
    )
  if (url.searchParams.has("error"))
    throw new Error("Google sign-in was cancelled or denied.")
  const codes = url.searchParams.getAll("code")
  if (codes.length !== 1 || !codes[0])
    throw new Error("Google did not return a sign-in code.")
  return codes[0]
}
export const base64url = (bytes: Uint8Array) =>
  btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "")
