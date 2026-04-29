"use client";

import { useState, useCallback } from "react";
import TopNav from "@/components/TopNav";
import TierBadge from "@/components/TierBadge";
import { usePolling } from "@/hooks/usePolling";
import type { DraftsResponse, DraftRow } from "@/lib/types";

const REASON_CHIPS = [
  "wrong-tone",
  "off-topic",
  "too-long",
  "regen",
  "wrong-person",
];

function ScoreBar({ score }: { score: number | null }) {
  if (score === null) return null;
  return (
    <div className="flex items-center gap-2 font-mono text-[11px] text-ink-3">
      <span className="uppercase text-[10px] tracking-wider">score</span>
      <span className="text-ink text-[13px]">{score}</span>
      <div className="flex-1 h-1 bg-bg-3 rounded-full overflow-hidden">
        <div
          className="h-full bg-ink rounded-full"
          style={{ width: `${score}%` }}
        />
      </div>
      <span className="text-ink-3 text-[10px]">/100</span>
    </div>
  );
}

function DraftCard({
  draft,
  onApprove,
  onReject,
}: {
  draft: DraftRow;
  onApprove: () => void;
  onReject: (reason: string, notes: string) => void;
}) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [notes, setNotes] = useState("");

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto">
        <div className="bg-bg-1 border-b border-line p-3.5 px-4 flex flex-col gap-2.5">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[13px] text-ink">
              {draft.company}
            </span>
            <TierBadge tier={draft.role_tier} />
            <span className="font-mono text-[11px] text-ink-3 ml-auto">
              {draft.days_in_column ? `${draft.days_in_column}d ago` : "today"}
            </span>
          </div>
          <div className="text-ink-2 text-xs">
            {draft.role_title} · @{draft.person_name}
          </div>

          <ScoreBar score={draft.manager_score} />

          {draft.relationship_type && (
            <>
              <div className="uppercase text-[10px] tracking-wider text-ink-3 mt-1">
                why this person
              </div>
              <div className="font-mono text-[11px] text-ink-3">
                · {draft.relationship_type}
              </div>
            </>
          )}

          {draft.hook && (
            <>
              <div className="uppercase text-[10px] tracking-wider text-ink-3 mt-1">
                hook
              </div>
              <div className="italic border-l-2 border-ink-4 pl-2.5 py-1 text-ink-2 text-[13px]">
                &ldquo;{draft.hook}&rdquo;
              </div>
            </>
          )}

          <div className="uppercase text-[10px] tracking-wider text-ink-3 mt-1">
            body
          </div>
          <div className="font-mono text-xs text-ink whitespace-pre-wrap bg-bg border border-line rounded p-2.5 leading-relaxed">
            {draft.body_text || "[ no draft body ]"}
          </div>

          {draft.subject && (
            <>
              <div className="uppercase text-[10px] tracking-wider text-ink-3 mt-1">
                subject
              </div>
              <div className="font-mono text-xs text-ink-2">
                {draft.subject}
              </div>
            </>
          )}
        </div>

        {/* reject panel */}
        {rejecting && (
          <div className="p-4 border-b border-line bg-bg-1">
            <div className="uppercase text-[10px] tracking-wider text-ink-3 mb-1.5">
              reject — quick reasons
            </div>
            <div className="flex gap-1.5 flex-wrap mb-2">
              {REASON_CHIPS.map((r) => (
                <button
                  key={r}
                  onClick={() => setReason(r)}
                  className={`px-2 py-[3px] text-[11px] font-mono rounded-xl border cursor-pointer ${
                    reason === r
                      ? "text-ink border-ink-3 bg-bg-2"
                      : "text-ink-2 border-line-2"
                  }`}
                >
                  {r}
                </button>
              ))}
            </div>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              placeholder="optional note..."
              className="w-full bg-bg border border-line-2 rounded text-ink font-mono text-xs p-2 outline-none focus:border-ink-3 resize-none"
            />
            <div className="flex gap-2 mt-2">
              <button
                onClick={() => {
                  setRejecting(false);
                  setReason("");
                  setNotes("");
                }}
                className="font-mono text-xs border border-line-2 rounded px-2.5 py-1.5 text-ink hover:border-ink-3 cursor-pointer"
              >
                cancel
              </button>
              <button
                onClick={() => {
                  onReject(reason, notes);
                  setRejecting(false);
                  setReason("");
                  setNotes("");
                }}
                className="font-mono text-xs rounded px-2.5 py-1.5 bg-ink text-bg border border-ink cursor-pointer ml-auto hover:bg-white"
              >
                confirm reject
              </button>
            </div>
          </div>
        )}
      </div>

      {/* sticky bottom CTAs */}
      <div className="sticky bottom-0 bg-gradient-to-t from-bg from-30% to-transparent p-4 grid grid-cols-2 gap-2.5 shrink-0">
        <button
          onClick={() => setRejecting(true)}
          className="h-14 rounded-[10px] border border-line-2 bg-transparent text-ink font-mono text-[13px] tracking-wide cursor-pointer hover:border-ink-4"
        >
          REJECT
        </button>
        <button
          onClick={onApprove}
          className="h-14 rounded-[10px] bg-ink text-bg border border-ink font-mono text-[13px] tracking-wide cursor-pointer hover:bg-white"
        >
          APPROVE
        </button>
      </div>
    </div>
  );
}

export default function DraftsPage() {
  const { data, refetch } = usePolling<DraftsResponse>("/api/drafts");
  const [currentIdx, setCurrentIdx] = useState(0);
  const [acting, setActing] = useState(false);

  const drafts = data?.drafts || [];
  const draft = drafts[currentIdx];

  const advance = useCallback(() => {
    setCurrentIdx((i) => Math.min(i + 1, drafts.length - 1));
  }, [drafts.length]);

  const handleAction = useCallback(
    async (action: "approve" | "reject", reason?: string, notes?: string) => {
      if (!draft || acting) return;
      setActing(true);
      try {
        const body: Record<string, string> = { action };
        if (reason) body.reason = reason;
        if (notes) body.notes = notes;
        await fetch(`/api/drafts/${draft.id}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        await refetch();
        // After refetch, stay at same index or clamp
        setCurrentIdx((i) => Math.min(i, Math.max(0, drafts.length - 2)));
      } finally {
        setActing(false);
      }
    },
    [draft, acting, refetch, drafts.length]
  );

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

  if (drafts.length === 0) {
    return (
      <div className="h-screen flex flex-col">
        <TopNav />
        <div className="flex-1 flex items-center justify-center font-mono text-ink-3 text-xs">
          no drafts awaiting approval
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col max-w-lg mx-auto w-full">
      {/* top bar */}
      <div className="px-4 pt-3.5 pb-2.5 border-b border-line flex items-center gap-2.5 shrink-0">
        <span className="font-mono text-[13px]">drafts</span>
        <span className="font-mono text-[11px] text-ink-3">
          · awaiting approval
        </span>
        <span className="font-mono text-[11px] text-ink-3 ml-auto">
          {currentIdx + 1} / {drafts.length}
        </span>
      </div>

      {draft && (
        <DraftCard
          key={draft.id}
          draft={draft}
          onApprove={() => handleAction("approve")}
          onReject={(reason, notes) => handleAction("reject", reason, notes)}
        />
      )}
    </div>
  );
}
