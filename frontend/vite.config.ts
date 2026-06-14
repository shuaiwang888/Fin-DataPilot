import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// GitHub Pages deploys project sites to https://<user>.github.io/<repo>/
// so we need base: "/Fin-DataPilot/" to make asset URLs resolve correctly.
// Override with VITE_BASE env var if you deploy to a custom domain.
const base = process.env.VITE_BASE || "/Fin-DataPilot/";

export default defineConfig({
  plugins: [react()],
  base,
  server: {
    port: 5173,
    host: true,
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
