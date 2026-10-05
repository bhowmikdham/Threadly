// The installed ZIP is authoritative, including after an automatic rollback.
const labels = document.querySelectorAll("[data-extension-version]");
const downloads = document.querySelectorAll("[data-extension-download]");
if (labels.length || downloads.length) {
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
    .then(({ version, sha256 }) => {
      if (typeof version !== "string" || !/^\d+(?:\.\d+){0,3}$/.test(version)) return;
      if (typeof sha256 !== "string" || !/^[a-f0-9]{64}$/.test(sha256)) return;
      // A version can be rebuilt (or rolled back) without changing its number.
      // A new URL bypasses copies of the old, unversioned ZIP already in caches.
      downloads.forEach((link) => {
        link.href = `/downloads/threadly-extension.zip?sha256=${sha256}`;
      });
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
