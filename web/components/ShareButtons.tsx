"use client";

import { useState } from "react";

// The share URL is built on the server (SITE_URL, or the request host) and
// passed in, so the X/LinkedIn links are correct in the server-rendered HTML
// and there's no hydration mismatch from reading window.location.
export default function ShareButtons({ shareUrl, shareText }: { shareUrl: string; shareText: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard unavailable (e.g. insecure context): fall back to selecting the URL.
      window.prompt("Copy this link:", shareUrl);
    }
  }

  const x = `https://twitter.com/intent/tweet?${new URLSearchParams({ text: shareText, url: shareUrl })}`;
  const linkedIn = `https://www.linkedin.com/sharing/share-offsite/?${new URLSearchParams({ url: shareUrl })}`;
  const button =
    "inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-border bg-surface px-3 text-sm font-medium text-foreground transition-colors hover:border-brand-border hover:text-brand";

  return (
    <div className="flex flex-wrap gap-2">
      <button type="button" onClick={copy} className={button} aria-live="polite">
        <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true" fill="none">
          <path d="M6.5 9.5l3-3M7 4.5l1-1a2.8 2.8 0 014 4l-1 1M9 11.5l-1 1a2.8 2.8 0 01-4-4l1-1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        {copied ? "Link copied" : "Copy link"}
      </button>
      <a href={x} target="_blank" rel="noopener noreferrer" className={button} aria-label="Share on X">
        Share on X
      </a>
      <a href={linkedIn} target="_blank" rel="noopener noreferrer" className={button} aria-label="Share on LinkedIn">
        Share on LinkedIn
      </a>
    </div>
  );
}
