/** Only extension pages use this bridge. Credentials remain in the service worker. */
export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status = 0
  ) {
    super(message)
  }
}
export async function bridge<T = any>(
  message: Record<string, unknown>
): Promise<T> {
  const result = await chrome.runtime.sendMessage({
    channel: "threadly",
    ...message
  })
  if (!result?.ok)
    throw new ApiError(
      result?.error?.code || "connection_failed",
      result?.error?.message ||
        "Threadly could not connect. Check your connection and try again.",
      result?.error?.status
    )
  return result.data
}
export const api = <T = any>(path: string, body?: unknown, method?: string) =>
  bridge<T>({
    type: "API",
    path,
    body,
    method: method || (body === undefined ? "GET" : "POST")
  })
export const requestId = () => crypto.randomUUID()
export const errorText = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "Something went wrong. Please try again."
export function addresses(value: string): string[] {
  const result = value
    .split(/[,;\n]/)
    .map((x) => x.trim())
    .filter(Boolean)
  if (result.some((x) => !/^[^\s<>@]+@[^\s<>@]+$/.test(x)))
    throw new Error("Enter email addresses separated by commas.")
  if (new Set(result.map((x) => x.toLowerCase())).size !== result.length)
    throw new Error("Remove duplicate recipients.")
  return result
}

export const actionReference = (
  artifactId: string,
  kind: "email" | "calendar",
  actionId?: string
) =>
  bridge<string | null>({
    type: "ACTION_REFERENCE",
    artifactId,
    kind,
    ...(actionId ? { actionId } : {})
  })
