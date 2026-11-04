// The RAAS Biotech company logo. Served from the public dir (no bundling).
// The PNG has a white background, so it is always rendered inside a white
// rounded tile — that keeps it readable on dark surfaces (dark sidebar,
// slate login card, dark loading screen) without needing a second asset.
export default function BrandLogo({ size = 'h-9 w-9', className = '' }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center overflow-hidden rounded-lg bg-white p-0.5 ring-1 ring-slate-200 dark:ring-slate-700 ${size} ${className}`.trim()}
    >
      <img
        src="/raas-logo.png"
        alt="RAAS Biotech logo"
        className="h-full w-full object-contain"
      />
    </span>
  );
}
