"use client";

import { useState, useEffect } from "react";
import TopNav from "@/components/TopNav";
import TierBadge from "@/components/TierBadge";
import Chip from "@/components/Chip";
import AgentChat from "@/components/AgentChat";
import { usePolling } from "@/hooks/usePolling";
import type { PipelineResponse, OutreachRow } from "@/lib/types";
import { PIPELINE_STATUSES, STATUS_LABELS } from "@/lib/types";

function timeSince(iso: string): string {
  if (!iso) return "";
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (d === 0) return "today";
  if (d === 1) return "1d ago";
  return `${d}d ago`;
}

function Card({
  row,
  onClick,
}: {
  row: OutreachRow;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left bg-bg-2 border rounded-[5px] p-2 px-2.5 cursor-pointer flex flex-col gap-1 hover:border-ink-4 hover:bg-bg-3 transition-colors ${
        row.stale ? "border-l-2 border-l-warn border-t-line-2 border-r-line-2 border-b-line-2" : "border-line-2"
      }`}
    >
      <div className="flex items-center gap-1.5 text-xs">
        <span className="font-medium text-ink truncate">{row.company}</span>
        <TierBadge tier={row.role_tier} />
      </div>
      <div className="text-ink-2 text-[11px] truncate">{row.role_title}</div>
      <div className="flex items-center gap-1.5 mt-0.5">
        <span className="text-ink-3 font-mono text-[10px] truncate">
          @ {row.person_name}
        </span>
        <span className="text-ink-4 font-mono text-[10px] ml-auto whitespace-nowrap">
          {timeSince(row.last_updated)}
        </span>
      </div>
    </button>
  );
}

function Drawer({
  row,
  onClose,
}: {
  row: OutreachRow;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<"details" | "chat">("details");

  // Reset to details when a different card is opened
  useEffect(() => {
    setTab("details");
  }, [row.id]);

  const tabClass = (t: "details" | "chat") =>
    `font-mono text-xs px-3 py-2 cursor-pointer border-b-2 transition-colors ${
      tab === t
        ? "text-ink border-ink"
        : "text-ink-3 border-transparent hover:text-ink-2"
    }`;

  return (
    <>
      <div
        className="absolute inset-0 bg-black/50 z-10"
        onClick={onClose}
      />
      <div className="absolute top-0 right-0 bottom-0 w-[380px] bg-bg-1 border-l border-line z-20 flex flex-col">
        {/* header */}
        <div className="p-3.5 px-4 border-b border-line flex items-center">
          <div className="flex-1 min-w-0">
            <div className="font-mono text-xs text-ink-3 truncate">{row.id}</div>
            <div className="text-base mt-0.5">
              {row.company}{" "}
              <span className="text-ink-2">· {row.role_title}</span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="ml-auto font-mono text-xs border border-line-2 rounded px-2.5 py-1.5 text-ink hover:border-ink-3 cursor-pointer"
          >
            esc
          </button>
        </div>

        {/* tab bar */}
        <div className="flex border-b border-line shrink-0">
          <button className={tabClass("details")} onClick={() => setTab("details")}>
            details
          </button>
          <button className={tabClass("chat")} onClick={() => setTab("chat")}>
            chat
          </button>
        </div>

        {/* tab content */}
        {tab === "details" ? (
          <div className="flex-1 overflow-y-auto p-4">
            <Row label="person" value={row.person_name} />
            <Row label="title" value={row.person_title} />
            <Row label="tier" value={<TierBadge tier={row.role_tier} />} />
            <Row label="status" value={<span className="font-mono">{row.status}</span>} />
            <Row label="email" value={row.email || <span className="text-ink-4">none</span>} />
            <Row label="confidence" value={row.email_confidence} />
            <Row label="last action" value={<span className="font-mono">{timeSince(row.last_updated)}</span>} />
            <Row
              label="stale"
              value={
                row.stale ? (
                  <span className="text-warn">
                    yes — {row.days_in_column}d in column
                  </span>
                ) : (
                  <span className="text-ink-3">no</span>
                )
              }
            />

            <div className="mt-4 uppercase text-[11px] tracking-wider text-ink-3">
              timeline
            </div>
            <div className="flex flex-col gap-1.5 mt-2">
              {PIPELINE_STATUSES.map((s) => {
                const idx = PIPELINE_STATUSES.indexOf(row.status as typeof s);
                const si = PIPELINE_STATUSES.indexOf(s);
                const klass =
                  si < idx ? "done" : si === idx ? "current" : "todo";
                return (
                  <div
                    key={s}
                    className={`flex gap-2 items-center font-mono text-[11px] px-2 py-1 rounded-[3px] border ${
                      klass === "done"
                        ? "text-ink-2 border-line"
                        : klass === "current"
                          ? "text-ink border-ink-4 bg-bg-2"
                          : "text-ink-4 border-dashed border-line"
                    }`}
                  >
                    <span className="w-3.5 text-ink-4">
                      {klass === "done" ? "\u2713" : klass === "current" ? "\u25B8" : "\u00B7"}
                    </span>
                    <span>{STATUS_LABELS[s]}</span>
                  </div>
                );
              })}
            </div>

            {row.hook && (
              <>
                <div className="mt-4 uppercase text-[11px] tracking-wider text-ink-3">
                  hook
                </div>
                <div className="mt-1.5 italic border-l-2 border-ink-4 pl-2.5 py-1 text-ink-2 text-[13px]">
                  &ldquo;{row.hook}&rdquo;
                </div>
              </>
            )}

            {row.notes && (
              <>
                <div className="mt-4 uppercase text-[11px] tracking-wider text-ink-3">
                  notes
                </div>
                <div className="mt-1.5 font-mono text-[11px] text-ink-3 whitespace-pre-wrap">
                  {row.notes}
                </div>
              </>
            )}
          </div>
        ) : (
          <AgentChat row={row} />
        )}
      </div>
    </>
  );
}

function Row({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex gap-2 py-1.5 border-b border-dashed border-line">
      <div className="w-24 text-ink-3 font-mono text-[11px] shrink-0">
        {label}
      </div>
      <div className="text-ink text-xs flex-1 min-w-0">{value}</div>
    </div>
  );
}

export default function PipelinePage() {
  const { data } = usePolling<PipelineResponse>("/api/pipeline");
  const [search, setSearch] = useState("");
  const [tierFilter, setTierFilter] = useState("all");
  const [drawerRow, setDrawerRow] = useState<OutreachRow | null>(null);

  const filterCards = (cards: OutreachRow[]) =>
    cards.filter((c) => {
      if (
        tierFilter !== "all" &&
        c.role_tier !== `T${tierFilter}` &&
        c.role_tier !== tierFilter
      )
        return false;
      if (
        search &&
        !`${c.company} ${c.role_title} ${c.person_name}`
          .toLowerCase()
          .includes(search.toLowerCase())
      )
        return false;
      return true;
    });

  return (
    <div className="h-screen flex flex-col relative">
      <TopNav />
      {/* toolbar */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-line bg-bg shrink-0">
        <div className="flex items-center gap-1.5 bg-bg-1 border border-line-2 rounded px-2 py-1 max-w-[280px] flex-1">
          <span className="font-mono text-ink-3">&#x2315;</span>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="search company / role / person"
            className="bg-transparent border-none outline-none text-ink font-mono text-xs w-full placeholder:text-ink-4"
          />
        </div>
        <div className="flex gap-1.5">
          {["all", "1", "2", "3"].map((t) => (
            <Chip
              key={t}
              active={tierFilter === t}
              onClick={() => setTierFilter(t)}
            >
              {t === "all" ? "all tiers" : `T${t}`}
            </Chip>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="font-mono text-[11px] text-ink-3">stale ≥ 5d</span>
          <span className="inline-block w-0.5 h-3 border-l-2 border-warn" />
        </div>
      </div>

      {/* kanban board */}
      <div className="flex-1 overflow-x-auto overflow-y-hidden">
        <div
          className="grid gap-3 p-3 h-full items-start"
          style={{
            gridAutoFlow: "column",
            gridAutoColumns: "260px",
          }}
        >
          {data &&
            PIPELINE_STATUSES.map((status) => {
              const all = data.columns[status] || [];
              const cards = filterCards(all);
              return (
                <div
                  key={status}
                  className="flex flex-col bg-bg-1 border border-line rounded-md h-full min-h-0"
                >
                  <div className="flex items-center gap-2 px-2.5 py-2 border-b border-line font-mono text-[11px]">
                    <span className="text-ink">{STATUS_LABELS[status]}</span>
                    <span className="text-ink-3 ml-auto">
                      {cards.length}/{all.length}
                    </span>
                  </div>
                  <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-2">
                    {cards.map((c) => (
                      <Card
                        key={c.id}
                        row={c}
                        onClick={() => setDrawerRow(c)}
                      />
                    ))}
                    {cards.length === 0 && (
                      <div className="text-ink-4 font-mono text-[11px] text-center py-4">
                        empty
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
        </div>
      </div>

      {/* drawer */}
      {drawerRow && (
        <Drawer row={drawerRow} onClose={() => setDrawerRow(null)} />
      )}
    </div>
  );
}
