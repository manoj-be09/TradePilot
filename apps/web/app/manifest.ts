import type { MetadataRoute } from "next";

export const dynamic = "force-static";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Market Systems Trading Platform",
    short_name: "Market Systems",
    description: "Trading research platform, starting with NIFTY options",
    start_url: "/",
    display: "standalone",
    background_color: "#f4f5ef",
    theme_color: "#182822",
    icons: [{ src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" }],
  };
}
