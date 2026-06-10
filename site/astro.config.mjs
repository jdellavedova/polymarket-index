import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";

export default defineConfig({
  site: "https://jdellavedova.com",
  output: "static",
  trailingSlash: "never",
  build: {
    assets: "_assets",
  },
  integrations: [sitemap()],
});
