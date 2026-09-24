import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto flex w-full max-w-xl flex-1 flex-col items-center justify-center px-4 py-24 text-center">
      <p className="font-mono text-sm text-subtle">404</p>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">Page not found</h1>
      <p className="mt-3 text-muted">
        Repository pages live at <span className="font-mono">/owner/repo</span>, for example{" "}
        <Link href="/pallets/click" className="font-mono text-brand hover:underline">
          /pallets/click
        </Link>
        .
      </p>
      <Link href="/" className="mt-6 rounded-lg bg-brand px-5 py-2.5 text-sm font-medium text-white hover:bg-brand-hover">
        Go to RepoLens
      </Link>
    </main>
  );
}
