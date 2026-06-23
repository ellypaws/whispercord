import path from "node:path";
import { defineConfig } from "vite";
import preact from "@preact/preset-vite";

export default defineConfig({
  base: "./",
  plugins: [preact()],
  build: {
    outDir: path.resolve(__dirname, ".."),
    emptyOutDir: false,
    cssCodeSplit: false,
    lib: {
      entry: path.resolve(__dirname, "overlay", "overlay.ts"),
      name: "VtOverlay",
      formats: ["iife"],
      fileName: () => "overlay.js",
    },
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
      },
    },
  },
});
