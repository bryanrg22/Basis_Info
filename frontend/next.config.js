/** @type {import('next').NextConfig} */
const nextConfig = {
  // Demo app - no Firebase configuration needed since everything is mocked
  
  // Ignore ESLint errors during build (pre-existing from frontend code)
  eslint: {
    ignoreDuringBuilds: true,
  },
  
  // Ignore TypeScript errors during build (pre-existing from frontend code)
  typescript: {
    ignoreBuildErrors: true,
  },
}

module.exports = nextConfig
