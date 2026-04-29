"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/", label: "pipeline" },
  { href: "/drafts", label: "drafts" },
  { href: "/agents", label: "activity" },
];

export default function TopNav({ extra }: { extra?: string }) {
  const pathname = usePathname();

  return (
    <div className="flex items-center h-11 px-4 border-b border-line bg-bg-1 gap-6 font-mono text-xs shrink-0">
      <div className="text-ink">~/outreach</div>
      <div className="flex gap-[18px]">
        {links.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className={
              pathname === l.href ? "text-ink" : "text-ink-3 hover:text-ink-2"
            }
          >
            {l.label}
          </Link>
        ))}
      </div>
      <div className="ml-auto text-ink-3">
        {extra ? `${extra} · ` : ""}localhost:3000
      </div>
    </div>
  );
}
