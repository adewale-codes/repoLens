import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import LensMark from "@/components/LensMark";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

const siteUrl = process.env.SITE_URL || "http://localhost:3000";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: "RepoLens: ask any GitHub repo how it works",
  description:
    "Paste a GitHub repository and get answers about its code, grounded in the source with line-level citations, plus a map of how its files depend on each other.",
  openGraph: { siteName: "RepoLens", type: "website" },
  twitter: { card: "summary_large_image" },
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col bg-background font-sans text-foreground">
        <header className="border-b border-border bg-surface/80 backdrop-blur">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3.5">
            <Link href="/" className="flex items-center gap-2 text-[15px] font-semibold tracking-tight">
              <span className="text-brand">
                <LensMark />
              </span>
              RepoLens
            </Link>
            <Link href="/" className="text-sm text-muted transition-colors hover:text-foreground">
              Index a repo
            </Link>
          </div>
        </header>
        <div className="flex flex-1 flex-col">{children}</div>
        <footer className="border-t border-border py-6">
          <div className="mx-auto flex max-w-6xl flex-col gap-1 px-4 text-xs text-muted sm:flex-row sm:justify-between">
            <span>Answers come only from the indexed code. Every citation is checked against what the model was shown.</span>
            <span>Python and JavaScript/TypeScript repositories.</span>
          </div>
        </footer>
      </body>
    </html>
  );
}
