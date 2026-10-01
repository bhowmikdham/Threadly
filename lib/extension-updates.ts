type InstallDetails = { reason: string; previousVersion?: string }
type NoticeEffects = {
  read: () => Promise<string | undefined>
  save: (version: string) => Promise<void>
  open: () => Promise<void>
}

/** Chrome fires update events for unpacked Reload too. Compare numeric versions. */
export function isVersionUpgrade(
  previous: string | undefined,
  current: string
) {
  const valid = (v: string) =>
    /^\d+(?:\.\d+){0,3}$/.test(v) &&
    v.split(".").every((n) => Number(n) <= 65535)
  if (!previous || !valid(previous) || !valid(current)) return false
  const before = previous.split(".").map(Number),
    after = current.split(".").map(Number)
  for (let i = 0; i < 4; i++) {
    const delta = (after[i] || 0) - (before[i] || 0)
    if (delta) return delta > 0
  }
  return false
}

export async function showUpdateNotice(
  details: InstallDetails,
  version: string,
  effects: NoticeEffects
) {
  if (
    details.reason !== "update" ||
    !isVersionUpgrade(details.previousVersion, version)
  )
    return false
  if ((await effects.read()) === version) return false
  await effects.open()
  await effects.save(version)
  return true
}
