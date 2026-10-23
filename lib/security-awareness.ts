/** Local awareness only. A subject pattern never authenticates a sender or alert. */
export const SECURITY_STATE_KEY = "threadlySecurityAwarenessV1"
export const DAY = 86_400_000
export const RETENTION = 30 * DAY
export const ACK_CAP = 1000
const ACCOUNT_CAP = 100
const digest = /^[a-f0-9]{64}$/
export type Recognition = "recognised" | "dismissed"
type Account = { key: string; activatedAt: number; lastSeen: number }
type Ack = {
  key: string
  receivedAt: number
  acknowledgedAt: number
  state: Recognition
}
type State = {
  version: 1
  salt: number[]
  accounts: Account[]
  acknowledgements: Ack[]
}
export type Incoming = {
  message_id: string
  received_at: string
  timestamp_source: "gmail.internalDate"
}
export type Awareness = { key: string; receivedAt: number }
type Store = { read(): Promise<unknown>; write(value: State): Promise<void> }
const instant = (n: unknown): n is number =>
  typeof n === "number" && Number.isSafeInteger(n) && n > 0

export function possibleSecuritySubject(subject: string): boolean {
  // Conservative English cues in the already displayed subject, never stored or sent.
  if (subject.length > 500 || /[\u0000-\u001f]/.test(subject)) return false
  const text = subject.trim()
  if (
    /^(re|fw|fwd)\s*:/i.test(text) ||
    /\b(newsletter|digest|weekly|webinar|sale|offer|tips|training|guide)\b/i.test(
      text
    )
  )
    return false
  return /^(?:(?:critical |important )?security alert(?:\b|:)|(?:new|unrecogni[sz]ed|suspicious|unusual) (?:sign[ -]?in|login|account activity)\b|(?:your )?password (?:was |has been )?(?:changed|reset)\b|(?:new|unrecogni[sz]ed) device (?:sign[ -]?in|login)\b)/i.test(
    text
  )
}

export function incomingMetadata(
  data: any,
  thread: string,
  last: string
): Incoming | null {
  const m = data?.latest_incoming
  if (
    !/^[a-f0-9]{1,32}$/.test(last) ||
    data?.thread_id !== thread ||
    data.source !== "live_gmail" ||
    data.persisted !== false ||
    !Array.isArray(data.source_message_ids) ||
    !data.source_message_ids.includes(last) ||
    m?.message_id !== last ||
    m.timestamp_source !== "gmail.internalDate" ||
    typeof m.received_at !== "string" ||
    !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/.test(
      m.received_at
    ) ||
    !Number.isFinite(Date.parse(m.received_at))
  )
    return null
  return m
}

function stateValue(raw: unknown): State {
  const s = raw as State
  if (
    s?.version !== 1 ||
    !Array.isArray(s.salt) ||
    s.salt.length !== 32 ||
    !s.salt.every((n) => Number.isInteger(n) && n >= 0 && n <= 255) ||
    !Array.isArray(s.accounts) ||
    s.accounts.length > ACCOUNT_CAP ||
    !Array.isArray(s.acknowledgements) ||
    s.acknowledgements.length > ACK_CAP ||
    !s.accounts.every(
      (a) =>
        digest.test(a.key) &&
        instant(a.activatedAt) &&
        instant(a.lastSeen) &&
        a.lastSeen >= a.activatedAt
    ) ||
    !s.acknowledgements.every(
      (a) =>
        digest.test(a.key) &&
        instant(a.receivedAt) &&
        instant(a.acknowledgedAt) &&
        ["recognised", "dismissed"].includes(a.state)
    ) ||
    new Set(s.accounts.map((a) => a.key)).size !== s.accounts.length ||
    new Set(s.acknowledgements.map((a) => a.key)).size !==
      s.acknowledgements.length
  )
    throw new Error("Security awareness storage unavailable")
  // Only this allowlist is saved; no source details can enter durable state.
  return {
    version: 1,
    salt: [...s.salt],
    accounts: s.accounts.map((a) => ({
      key: a.key,
      activatedAt: a.activatedAt,
      lastSeen: a.lastSeen
    })),
    acknowledgements: s.acknowledgements.map((a) => ({
      key: a.key,
      receivedAt: a.receivedAt,
      acknowledgedAt: a.acknowledgedAt,
      state: a.state
    }))
  }
}

export class SecurityAwarenessStore {
  private tail: Promise<unknown> = Promise.resolve()
  constructor(
    private store: Store,
    private clock = Date.now,
    private cryptoApi: Crypto = crypto
  ) {}
  private serial<T>(run: () => Promise<T>): Promise<T> {
    const result = this.tail.then(run)
    this.tail = result.catch(() => undefined)
    return result
  }
  private async key(s: State, parts: string[]) {
    const key = await this.cryptoApi.subtle.importKey(
      "raw",
      new Uint8Array(s.salt),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"]
    )
    const bytes = await this.cryptoApi.subtle.sign(
      "HMAC",
      key,
      new TextEncoder().encode(JSON.stringify(parts))
    )
    return Array.from(new Uint8Array(bytes), (b) =>
      b.toString(16).padStart(2, "0")
    ).join("")
  }
  private async load(now: number): Promise<State> {
    const raw = await this.store.read()
    const s: State =
      raw === undefined
        ? {
            version: 1,
            salt: Array.from(
              this.cryptoApi.getRandomValues(new Uint8Array(32))
            ),
            accounts: [],
            acknowledgements: []
          }
        : stateValue(raw)
    if (
      s.accounts.some((a) => a.lastSeen > now) ||
      s.acknowledgements.some((a) => a.acknowledgedAt > now)
    )
      throw new Error("Security awareness clock unavailable")
    s.accounts = s.accounts.filter(
      (a) =>
        a.lastSeen > now - RETENTION &&
        a.lastSeen <= now &&
        a.activatedAt <= now
    )
    s.acknowledgements = s.acknowledgements.filter(
      (a) => a.acknowledgedAt > now - RETENTION && a.acknowledgedAt <= now
    )
    return s
  }
  /** Purge expired state on worker startup as well as each use; create nothing. */
  prune(): Promise<void> {
    return this.serial(async () => {
      if ((await this.store.read()) !== undefined)
        await this.store.write(await this.load(this.clock()))
    })
  }
  /** scope is authenticated origin/user/mailbox; only its keyed hash is retained. */
  check(
    scope: string,
    metadata: Incoming | null,
    state?: Recognition
  ): Promise<Awareness | null> {
    return this.serial(async () => {
      const now = this.clock(),
        s = await this.load(now)
      const accountKey = await this.key(s, ["account", scope])
      let account = s.accounts.find((a) => a.key === accountKey)
      if (!account) {
        if (s.accounts.length >= ACCOUNT_CAP) return null
        account = { key: accountKey, activatedAt: now, lastSeen: now }
        s.accounts.push(account)
      }
      account.lastSeen = now
      const receivedAt = Date.parse(metadata?.received_at || "")
      // Recent arrivals remain eligible even when they predate installation.
      // Keep the existing account state so upgrades preserve acknowledgements.
      const recent = metadata && receivedAt <= now && receivedAt > now - DAY
      const key = metadata
        ? await this.key(s, ["message", scope, metadata.message_id])
        : null
      const acknowledged = key && s.acknowledgements.some((a) => a.key === key)
      let result: Awareness | null = null
      if (recent && acknowledged && state) result = { key, receivedAt }
      if (recent && !acknowledged && s.acknowledgements.length < ACK_CAP) {
        result = { key, receivedAt }
        if (state)
          s.acknowledgements.push({
            key,
            receivedAt,
            acknowledgedAt: now,
            state
          })
      }
      // Persist account state before presenting or confirming a question.
      await this.store.write(s)
      return result
    })
  }
}
