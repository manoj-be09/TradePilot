import type { Metadata, Viewport } from "next";
import "./styles.css";

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#182822",
};

export const metadata: Metadata = {
  title: "TradingPilot",
  description: "Trading research platform, starting with NIFTY options",
  applicationName: "TradingPilot",
  icons: { icon: "/icon.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
