const tierColors: Record<string, string> = {
  T1: "text-tier-1 border-tier-1",
  T2: "text-tier-2 border-tier-2",
  T3: "text-tier-3 border-tier-3",
};

export default function TierBadge({ tier }: { tier: string }) {
  const t = tier.startsWith("T") ? tier : `T${tier}`;
  const cls = tierColors[t] || tierColors.T3;
  return (
    <span
      className={`inline-flex items-center px-1.5 py-px font-mono text-[10px] rounded-[3px] border leading-none tracking-wide ${cls}`}
    >
      {t}
    </span>
  );
}
