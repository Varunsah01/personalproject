"use client";

import { useState, useCallback } from "react";
import TopNav from "@/components/TopNav";
import TierBadge from "@/components/TierBadge";
import { usePolling } from "@/hooks/usePolling";
import type { LinkedInQueueResponse, DraftRow } from "@/lib/types";

function stripFrontmatter(text: string): string {
  if (!text.startsWith("---")) return text;
  const end = text.indexOf("---", 3);
  if (end === -1) return text;
  return text.slice(end + 3).trim();
}

function LinkedInCard({
  row,
  onMarkMessaged,
}: {
  row: DraftRow;
  onMarkMessaged: () => void;
}) {
  const dmText = row.body_text ? stripFrontmatter(row.body_text) : "";
  const hasDraft = dmText.length > 0;

  const handleSend = useCallback(() => {
    // 1. Open LinkedIn profile in new tab
    if (row.person_linkedin) {
      window.open(row.person_linkedin, "_blank");
    }
    // 2. Copy DM to clipboard
    if (hasDraft) {
      navigator.clipboard.writeText(dmText).catch(() => {
        // clipboard API may fail in non-HTTPS contexts; ignore
      });
    }
    // 3. Mark as messaged
    onMarkMessaged();
  }, [row.person_linkedin, dmText, hasDraft, onMarkMessaged]);

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto">
        <div className="bg-bg-1 border-b border-line p-3.5 px-4 flex flex-col gap-2.5">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[13px] text-ink">
              {row.company}
            </span>
            <TierBadge tier={row.role_tier} />
            <span className="font-mono text-[11px] text-ink-3 ml-auto">
              {row.days_in_column ? `${row.days_in_column}d ago` : "today"}
            </span>
          </div>

          <div className="text-ink-2 text-xs">
            {row.role_title} {"\u00B7"} @{row.person_name}
          </div>

          {row.person_title && (
            <div className="font-mono text-[11px] text-ink-3">
              {row.person_title}
            </div>
          )}

          {row.person_linkedin && (
            <>
              <div className="uppercase text-[10px] tracking-wider text-ink-3 mt-1">
                linkedin
              </div>
              <a
                href={row.person_linkedin}
                target="_blank"
                rel="noopener noreferrer"
                className="font-mono text-[11px] text-ink-2 hover:text-ink underline break-all"
              >
                {row.person_linkedin}
              </a>
            </>
          )}

          {row.hook && (
            <>
              <div className="uppercase text-[10px] tracking-wider text-ink-3 mt-1">
                hook
              </div>
              <div className="italic border-l-2 border-ink-4 pl-2.5 py-1 text-ink-2 text-[13px]">
                &ldquo;{row.hook}&rdquo;
              </div>
            </>
          )}

          <div className="uppercase text-[10px] tracking-wider text-ink-3 mt-1">
            dm
          </div>
          <div className="font-mono text-xs text-ink whitespace-pre-wrap bg-bg border border-line rounded p-2.5 leading-relaxed">
            {hasDraft ? dmText : "[ no DM drafted yet ]"}
          </div>

          {row.relationship_type && (
            <>
              <div className="uppercase text-[10px] tracking-wider text-ink-3 mt-1">
                relationship
              </div>
              <div className="font-mono text-[11px] text-ink-3">
                {"\u00B7"} {row.relationship_type}
              </div>
            </>
          )}
        </div>
      </div>

      {/* sticky bottom CTA */}
      <div className="sticky bottom-0 bg-gradient-to-t from-bg from-30% to-transparent p-4 shrink-0">
        <button
          onClick={handleSend}
          disabled={!hasDraft}
          className={`w-full h-14 rounded-[10px] font-mono text-[13px] tracking-wide cursor-pointer ${
            hasDraft
              ? "bg-ink text-bg border border-ink hover:bg-white"
              : "bg-bg-2 text-ink-3 border border-line-2 cursor-not-allowed"
          }`}
        >
          {hasDraft ? "OPEN LINKEDIN + COPY DM" : "WAITING FOR DM DRAFT"}
        </button>
      </div>
    </div>
  );
}

export default function LinkedInPage() {
  const { data, refetch } = usePolling<LinkedInQueueResponse>(
    "/api/linkedin-queue"
  );
  const [currentIdx, setCurrentIdx] = useState(0);
  const [acting, setActing] = useState(false);

  const rows = data?.rows || [];
  const row = rows[currentIdx];

  const handleMarkMessaged = useCallback(async () => {
    if (!row || acting) return;
    setActing(true);
    try {
      await fetch(`/api/linkedin-queue/${row.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notes: "" }),
      });
      await refetch();
      setCurrentIdx((i) => Math.min(i, Math.max(0, rows.length - 2)));
    } finally {
      setActing(false);
    }
  }, [row, acting, refetch, rows.length]);

  if (!data) {
    return (
      <div className="h-screen flex flex-col">
        <TopNav />
        <div className="flex-1 flex items-center justify-center font-mono text-ink-3 text-xs">
          loading...
        </div>
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="h-screen flex flex-col">
        <TopNav />
        <div className="flex-1 flex items-center justify-center font-mono text-ink-3 text-xs">
          no linkedin DMs pending
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col max-w-lg mx-auto w-full">
      <div className="px-4 pt-3.5 pb-2.5 border-b border-line flex items-center gap-2.5 shrink-0">
        <span className="font-mono text-[13px]">linkedin</span>
        <span className="font-mono text-[11px] text-ink-3">
          {"\u00B7"} DMs to send
        </span>
        <span className="font-mono text-[11px] text-ink-3 ml-auto">
          {currentIdx + 1} / {rows.length}
        </span>
      </div>

      {/* navigation between cards */}
      {rows.length > 1 && (
        <div className="flex items-center justify-between px-4 py-1.5 border-b border-line bg-bg-1">
          <button
            onClick={() => setCurrentIdx((i) => Math.max(0, i - 1))}
            disabled={currentIdx === 0}
            className="font-mono text-[11px] text-ink-3 hover:text-ink disabled:opacity-30 cursor-pointer disabled:cursor-default"
          >
            {"\u2190"} prev
          </button>
          <button
            onClick={() =>
              setCurrentIdx((i) => Math.min(rows.length - 1, i + 1))
            }
            disabled={currentIdx === rows.length - 1}
            className="font-mono text-[11px] text-ink-3 hover:text-ink disabled:opacity-30 cursor-pointer disabled:cursor-default"
          >
            next {"\u2192"}
          </button>
        </div>
      )}

      {row && (
        <LinkedInCard
          key={row.id}
          row={row}
          onMarkMessaged={handleMarkMessaged}
        />
      )}
    </div>
  );
}
