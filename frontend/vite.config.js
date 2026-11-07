import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { readFileSync, readdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'

const apiTarget = process.env.RAG_API_TARGET || 'http://127.0.0.1:8000'

function pdfAssets() {
  const require = createRequire(import.meta.url)
  const root = dirname(require.resolve('pdfjs-dist/package.json'))
  const files = new Map()
  // PDF.js requests these resources by their original names at render time.
  for (const directory of ['cmaps', 'standard_fonts', 'wasm']) {
    const folder = join(root, directory)
    for (const entry of readdirSync(folder, { withFileTypes: true })) {
      if (entry.isFile()) files.set(`pdfjs/${directory}/${entry.name}`, join(folder, entry.name))
    }
  }
  return {
    name: 'rag-pdf-assets',
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const path = request.url?.split('?')[0]
        const base = server.config.base
        const source = path?.startsWith(base) && files.get(path.slice(base.length))
        if (!source) return next()
        const type = source.endsWith('.wasm') ? 'application/wasm' :
          source.endsWith('.js') ? 'text/javascript' : 'application/octet-stream'
        response.setHeader('Content-Type', type)
        response.end(readFileSync(source))
      })
    },
    generateBundle() {
      for (const [fileName, source] of files) {
        this.emitFile({ type: 'asset', fileName, source: readFileSync(source) })
      }
    },
  }
}

export default defineConfig({
  plugins: [vue(), pdfAssets()],
  server: {
    port: 5173,
    proxy: {
      '/api': apiTarget,
    },
  },
  preview: {
    port: 4173,
    proxy: {
      '/api': apiTarget,
    },
  },
})

