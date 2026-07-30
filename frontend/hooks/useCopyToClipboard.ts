"use client";

/**
 * Extracted from `ErrorCard`'s inline copy-the-request-id logic in
 * ChatPane.tsx - the message toolbar's Copy button needs the identical
 * copy-then-flip-back-after-a-beat behaviour for a different payload (the
 * answer text), so this is the one place both now call into.
 */

import { useCallback, useState } from "react";

export function useCopyToClipboard(resetMs = 1500) {
  const [copied, setCopied] = useState(false);

  const copy = useCallback(
    async (text: string) => {
      try {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        window.setTimeout(() => setCopied(false), resetMs);
      } catch {
        // Clipboard access denied (permissions, insecure context) - the text
        // stays selectable either way, not worth surfacing as an error.
        setCopied(false);
      }
    },
    [resetMs],
  );

  return { copied, copy };
}
