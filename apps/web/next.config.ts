import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // pg is a server-only native-ish dep; keep it out of the client bundle graph.
  serverExternalPackages: ["pg"],
};

export default nextConfig;
