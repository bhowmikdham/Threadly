import { build } from "esbuild";
import { readFile, writeFile } from "node:fs/promises";
await build({ entryPoints: ["site.js"], bundle: true, format: "esm", target: "es2022",
  minify: true, legalComments: "linked", outfile: "../website/site.js",
  banner: { js: "/* Third-party notices: /third-party-notices.txt */" } });
const packages = ["motion", "framer-motion", "motion-dom", "motion-utils", "tslib"];
const notices = await Promise.all(packages.map(async name => {
  const file = name === "tslib" ? "LICENSE.txt" : "LICENSE.md";
  return name + "\n\n" + await readFile(`node_modules/${name}/${file}`, "utf8");
}));
await writeFile("../website/third-party-notices.txt", notices.join("\n\n---\n\n"));
