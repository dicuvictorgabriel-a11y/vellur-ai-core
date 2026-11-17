import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The production build is served by the FastAPI controller at /autopilot/react/.
// A relative base keeps the same build usable through the VELLUR preview proxy.
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    fs: {
      allow: [".."],
    },
    proxy: {
      "/autopilot": "http://127.0.0.1:4173",
    },
  },
  build: {
    outDir: "../autopilot/app/static/react",
    emptyOutDir: true,
    sourcemap: false,
    reportCompressedSize: true,
    rollupOptions: {
      output: {
        manualChunks: {
          carbon: ["@carbon/react", "@carbon/icons-react"],
        },
      },
    },
  },
});
