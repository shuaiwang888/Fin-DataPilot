import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The custom domain is configured on the account-level GitHub Pages site.
// Project sites retain their repository path below that domain.
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
    rollupOptions: {
      output: {
        // Keep dependencies in their natural graph; otherwise Rollup's
        // default behavior can pull all transitive Ant Design dependencies
        // into whichever manual chunk claims the first entry module.
        onlyExplicitManualChunks: true,
        manualChunks(id) {
          if (!id.includes("node_modules")) return;

          if (
            id.includes("react-markdown") ||
            id.includes("remark-") ||
            id.includes("micromark") ||
            id.includes("unified") ||
            id.includes("mdast-") ||
            id.includes("hast-") ||
            id.includes("vfile")
          ) {
            return "markdown-vendor";
          }

          if (
            id.includes("/react/") ||
            id.includes("/react-dom/") ||
            id.includes("/scheduler/") ||
            id.includes("/zustand/")
          ) {
            return "react-core";
          }

          // These packages are leaf-level UI runtimes with one-way imports,
          // so separating them is cache-friendly and does not split the
          // tightly coupled antd <-> rc-* component graph.
          if (id.includes("@ant-design/icons")) return "antd-icons";
          if (id.includes("@ant-design/x")) return "antd-x";
          if (
            id.includes("@ant-design/cssinjs") ||
            id.includes("@ant-design/fast-color")
          ) {
            return "antd-style-runtime";
          }

          // Ant Design and rc-* have internal cross-package dependencies.
          // Let Rollup keep those module graphs aligned with the lazy feature
          // boundaries instead of forcing circular package-level chunks.
          return;
        },
      },
    },
  },
});
