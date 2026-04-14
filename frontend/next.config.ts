import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  // Static export — required for GitHub Pages (no Node server)
  output: 'export',

  // GitHub Pages serves the app under /seo-project/
  basePath: '/seo-project',

  // next/image optimisation needs a server; disable for static export
  images: {
    unoptimized: true,
  },

  // Trailing slash so every page gets an index.html file
  trailingSlash: true,
}

export default nextConfig
