"use client";

export default function Chip({
  label,
  active,
  onClick,
  children,
}: {
  label?: string;
  active?: boolean;
  onClick?: () => void;
  children?: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-2 py-[3px] text-[11px] font-mono rounded-xl border cursor-pointer transition-colors ${
        active
          ? "text-ink border-ink-3 bg-bg-2"
          : "text-ink-2 border-line-2 hover:border-ink-4"
      }`}
    >
      {children || label}
    </button>
  );
}
