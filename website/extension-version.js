// The installed ZIP is authoritative, including after an automatic rollback.
const labels = document.querySelectorAll("[data-extension-version]");
if (labels.length) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  fetch("/downloads/threadly-extension.json", {
    cache: "no-store",
    credentials: "omit",
    signal: controller.signal,
  })
    .then((response) => {
      if (!response.ok) throw new Error("Version unavailable");
      return response.json();
    })
    .then(({ version }) => {
      if (typeof version !== "string" || !/^\d+(?:\.\d+){0,3}$/.test(version)) return;
      labels.forEach((label) => {
        label.textContent = `Version ${version}`;
        label.hidden = false;
      });
    })
    .catch(() => {
      // The download remains usable when release metadata is temporarily unavailable.
    })
    .finally(() => clearTimeout(timeout));
}
