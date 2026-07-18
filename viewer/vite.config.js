import { defineConfig } from "vite";
import { resolve } from "path";

export default defineConfig({
  server: { port: 5173 },
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        mesh: resolve(__dirname, "mesh.html"),
      },
    },
  },
});
