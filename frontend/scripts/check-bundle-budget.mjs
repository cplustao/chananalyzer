import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { gzipSync } from "node:zlib"

const dist = resolve("dist")
const html = readFileSync(resolve(dist, "index.html"), "utf8")
const assets = [...html.matchAll(/(?:src|href)="([^"]+\.(?:js|css))"/g)].map((match) => match[1])
const gzipSize = (asset) => gzipSync(readFileSync(resolve(dist, asset.replace(/^\//, "")))).byteLength
const initialJs = assets.filter((asset) => asset.endsWith(".js")).reduce((sum, asset) => sum + gzipSize(asset), 0)
const initialCss = assets.filter((asset) => asset.endsWith(".css")).reduce((sum, asset) => sum + gzipSize(asset), 0)
const limits = { js: 100 * 1024, css: 25 * 1024 }
console.log(`Initial JS gzip: ${(initialJs / 1024).toFixed(1)} KB / 100 KB`)
console.log(`Initial CSS gzip: ${(initialCss / 1024).toFixed(1)} KB / 25 KB`)
if (initialJs > limits.js || initialCss > limits.css) process.exitCode = 1