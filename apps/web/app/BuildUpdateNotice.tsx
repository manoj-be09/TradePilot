"use client";

import { useEffect, useState } from "react";

const CURRENT_BUILD = process.env.NEXT_PUBLIC_APP_BUILD_VERSION;

export function BuildUpdateNotice() {
  const [availableVersion, setAvailableVersion] = useState<string | null>(null);

  useEffect(() => {
    if (!CURRENT_BUILD) return;
    let stopped = false;

    async function check() {
      try {
        const response = await fetch(`/build-version.json?t=${Date.now()}`, { cache: "no-store" });
        if (!response.ok) return;
        const result = await response.json() as { version?: string };
        if (!stopped && result.version && result.version !== CURRENT_BUILD) {
          setAvailableVersion(result.version);
        }
      } catch {
        // An unavailable static host should not interrupt the trading workspace.
      }
    }

    void check();
    const timer = window.setInterval(() => void check(), 30_000);
    window.addEventListener("focus", check);
    return () => { stopped = true; window.clearInterval(timer); window.removeEventListener("focus", check); };
  }, []);

  if (!availableVersion) return null;
  return <div className="build-update-notice" role="status">
    <span>A newer version of the workspace is ready.</span>
    <a href={`/?build=${encodeURIComponent(availableVersion)}`}>Reload app</a>
  </div>;
}
