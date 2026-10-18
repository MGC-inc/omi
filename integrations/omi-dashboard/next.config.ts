import type { NextConfig } from "next";

// Every page here reads per-request secrets (Omi/Notion tokens) and renders
// user-specific data, so nothing in this app should be statically generated
// or pre-rendered at build time. Keeping the config otherwise plain (no
// experimental caching model) avoids the build trying to execute
// data-fetching code without a real request — see src/lib/*.ts, which all
// read env vars lazily at request time instead of at module load.
const nextConfig: NextConfig = {
  turbopack: {
    // This app lives inside the omi monorepo, which has its own
    // package-lock.json one level up; without this, Turbopack guesses the
    // workspace root from that outer lockfile instead of this directory.
    root: __dirname,
    // Tailwind v4's CSS-based config needs this loader under Turbopack.
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
