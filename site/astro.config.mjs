import { defineConfig } from "astro/config";

export default defineConfig({
  site: "https://jdellavedova.com",
  output: "static",
  trailingSlash: "never",
  build: {
    assets: "_assets",
  },
});
