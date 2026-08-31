"""Fail-fast production config validation. A service that would run with
placeholder secrets must refuse to start — a clear crash at boot beats a
silently forgeable JWT in production. No-op in dev mode."""

PLACEHOLDER_MARKERS = ("change-me", "dev-secret", "dev-internal", "threadly-dev-password")


def find_config_problems(
    dev_mode: bool,
    required: dict[str, str],
    min_lengths: dict[str, int] | None = None,
) -> list[str]:
    if dev_mode:
        return []
    min_lengths = min_lengths or {}
    problems = []
    for name, value in required.items():
        value = (value or "").strip()
        if not value:
            problems.append(f"{name} is not set")
        elif any(marker in value for marker in PLACEHOLDER_MARKERS):
            problems.append(f"{name} still has a placeholder value")
        elif name in min_lengths and len(value) < min_lengths[name]:
            problems.append(f"{name} must be at least {min_lengths[name]} characters")
    return problems


def assert_prod_config(
    service: str,
    dev_mode: bool,
    required: dict[str, str],
    min_lengths: dict[str, int] | None = None,
) -> None:
    problems = find_config_problems(dev_mode, required, min_lengths)
    if problems:
        raise RuntimeError(
            f"[{service}] refusing to start with unsafe production config: "
            + "; ".join(problems)
            + ". Fix .env (see .env.example) or set THREADLY_DEV_MODE=true for development."
        )
