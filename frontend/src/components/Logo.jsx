export default function Logo({ className = "h-12 w-auto" }) {
  return (
    <svg
      className={className}
      viewBox="0 0 120 60"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="Five a Day logo"
    >
      {/* Leaf / petal shapes in brand colors */}
      <ellipse cx="30" cy="30" rx="14" ry="22" fill="#7ed957" transform="rotate(-20 30 30)" />
      <ellipse cx="42" cy="28" rx="12" ry="20" fill="#b11fff" transform="rotate(10 42 28)" />
      <ellipse cx="22" cy="26" rx="10" ry="18" fill="#38b6ff" transform="rotate(-35 22 26)" />
      {/* Stem */}
      <path d="M30 48 Q28 55 32 58" stroke="#7ed957" strokeWidth="2.5" fill="none" strokeLinecap="round" />
      {/* Text */}
      <text x="58" y="28" fontFamily="Syne, sans-serif" fontWeight="700" fontSize="14" fill="#7615aa">
        five a
      </text>
      <text x="58" y="46" fontFamily="Syne, sans-serif" fontWeight="800" fontSize="16" fill="#b11fff">
        day
      </text>
    </svg>
  );
}
