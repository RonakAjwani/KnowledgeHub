import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emits a self-contained server bundle so the Docker image ships only what it
  // runs, rather than the whole node_modules tree — which matters when the
  // deployment target is a 512 MB box.
  output: "standalone",
};

export default nextConfig;
