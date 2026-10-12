"use client";

import { useEffect, useRef, useState } from "react";

type EntryIntent = { key: string; body: string };
type EntryOwner = { id: string; token: string };

function savedIntent(value: string | null): EntryIntent | null {
  if (value === null) return null;
  try {
    const intent: unknown = JSON.parse(value);
    if (intent && typeof intent === "object" && "key" in intent && "body" in intent &&
        typeof intent.key === "string" && /^[A-Za-z0-9_-]{8,128}$/.test(intent.key) &&
        typeof intent.body === "string") return intent as EntryIntent;
  } catch {
    // Preserve the stored value so a possibly completed entry can be investigated.
  }
  throw new Error("The saved paper entry cannot be read. Check the trade list before clearing this browser's session data.");
}

/** Preserve an uncertain entry across refresh/login and reuse its server deduplication key. */
export function usePaperEntry(token: string | null, apiUrl: string) {
  const [pending, setPending] = useState<EntryIntent | null>(null);
  const [owner, setOwner] = useState<EntryOwner | null>(null);
  const [identityError, setIdentityError] = useState("");
  const [identityRetry, setIdentityRetry] = useState(0);
  const identityVersion = useRef(0);
  const inFlight = useRef(false);

  useEffect(() => {
    const version = ++identityVersion.current;
    setOwner(null); setPending(null); setIdentityError("");
    if (!token) return;
    const controller = new AbortController();
    void fetch(`${apiUrl}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` }, signal: controller.signal,
    }).then(async response => {
      if (!response.ok) throw new Error("Could not verify your account. Check the API connection or sign in again.");
      const user = await response.json() as { id: string };
      if (controller.signal.aborted || version !== identityVersion.current) return;
      if (typeof user.id !== "string" || !user.id) throw new Error("The API returned an invalid account identity.");
      const intent = savedIntent(sessionStorage.getItem(`paper-entry:${user.id}`));
      setPending(intent);
      setOwner({ id: user.id, token });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted && version === identityVersion.current) {
        setIdentityError(error instanceof Error ? error.message : "Could not verify your account.");
      }
    });
    return () => { controller.abort(); ++identityVersion.current; };
  }, [apiUrl, token, identityRetry]);

  async function submit(body?: string) {
    if (!owner || owner.token !== token || !token) throw new Error("Wait for account verification before submitting a paper entry.");
    if (inFlight.current) throw new Error("A paper entry is already being submitted.");
    const version = identityVersion.current;
    const storageKey = `paper-entry:${owner.id}`;
    const previous = savedIntent(sessionStorage.getItem(storageKey)) || pending;
    if (previous && body !== undefined) {
      throw new Error("Resolve the pending entry before opening another position.");
    }
    const intent = previous || (body ? { key: crypto.randomUUID(), body } : null);
    if (!intent) throw new Error("No pending entry to retry.");
    sessionStorage.setItem(storageKey, JSON.stringify(intent));
    if (version === identityVersion.current) setPending(intent);
    inFlight.current = true;
    try {
      const response = await fetch(`${apiUrl}/paper/trades`, {
        method: "POST", body: intent.body,
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json",
          "Idempotency-Key": intent.key },
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({})) as { detail?: unknown };
        const detail = typeof error.detail === "string" ? error.detail :
          `Entry failed (HTTP ${response.status}). ${JSON.stringify(error.detail || "")}`;
        // A rejected request did not create a trade. A key conflict can mean the
        // saved request and server record differ, so retain it for investigation.
        const rejected = response.status >= 400 && response.status < 500 &&
          ![401, 408, 429].includes(response.status) &&
          detail !== "Idempotency key belongs to a different entry";
        if (rejected) {
          sessionStorage.removeItem(storageKey);
          if (version === identityVersion.current) setPending(null);
        }
        throw new Error(detail);
      }
      await response.json();
      sessionStorage.removeItem(storageKey);
      if (version === identityVersion.current) setPending(null);
    } finally {
      inFlight.current = false;
    }
  }

  const verified = owner?.token === token && !identityError;
  return {
    pending: verified ? pending : null,
    ready: verified,
    identityError,
    retryIdentity: () => setIdentityRetry(value => value + 1),
    submit,
  };
}
