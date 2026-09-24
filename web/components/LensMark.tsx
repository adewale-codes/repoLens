export default function LensMark({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" fill="none">
      <circle cx="10" cy="10" r="6.5" stroke="currentColor" strokeWidth="2.2" />
      <path d="M15 15l5.5 5.5" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
      <path d="M7.4 9.2l1.6 1.6 3-3.2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
