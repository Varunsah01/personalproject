"use client";

import { useState, useMemo } from "react";
import TopNav from "@/components/TopNav";
import StatusDot from "@/components/StatusDot";
import Chip from "@/components/Chip";
import { usePolling } from "@/hooks/usePolling";
import type { LogsResponse, AgentLog } from "@/lib/types";

const AGENTS = [
  "researcher",
  "person-finder",
  "contact-finder",
  "drafter",
  "sender",
];

const STATUS_FILTERS = ["all", "ok", "warn", "error"] as const;

function LogRow({ log }: { log: AgentLog }) {
  const bgClass =
    log.status === "error"
      ? "bg-err/[0.06]"
      : log.status === "warn"
        ? "bg-warn/[0.04]"
        : "";

  const statusColor =
    log.status === "error"
      ? "text-err"
      : log.status === "warn"
        ? "text-warn"
        : "text-ok";

  return (
    <div
      className={`grid gap-3 px-4 py-[5px] border-b border-dashed border-line font-mono text-xs items-center hover:bg-bg-1 ${bgClass}`}
      style={{ gridTemplateColumns: "90px 110px 130px 1fr 90px" }}
    >
      <div className="text-ink-3 truncate">{log.timestamp?.slice(11, 19) || ""}</div>
      <div className="text-ink-2 truncate">{log.agent}</div>
      <div className="text-ink truncate">{log.action}</div>
      <div className="truncate">
        <span className="text-ink-2">{log.target}</span>
        <span className="text-ink-3"> · {log.message}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <StatusDot status={log.status} />
        <span className={statusColor}>{log.status}</span>
      </div>
    </div>
  );
}

export default function ActivityPage() {
  const [paused, setPaused] = useState(false);
  const [agentFilter, setAgentFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [searchQ, setSearchQ] = useState("");

  const queryParams = useMemo(() => {
    const params = new URLSearchParams();
    if (agentFilter !== "all") params.set("agent", agentFilter);
    if (statusFilter !== "all") params.set("status", statusFilter);
    if (searchQ) params.set("q", searchQ);
    params.set("limit", "200");
    return params.toString();
  }, [agentFilter, statusFilter, searchQ]);

  const { data } = usePolling<LogsResponse>(
    `/api/logs?${queryParams}`,
    5000,
    paused
  );

  const logs = data?.logs || [];

  return (
    <div className="h-screen flex flex-col">
      <TopNav extra={`${logs.length} rows`} />

      {/* toolbar */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-line bg-bg font-mono text-xs shrink-0 flex-wrap">
        <button
          onClick={() => setPaused(!paused)}
          className="flex items-center gap-1.5 px-2.5 py-1.5 border border-line-2 rounded text-ink cursor-pointer hover:border-ink-3"
        >
          <span
            className={`inline-block w-1.5 h-1.5 rounded-full ${paused ? "bg-warn" : "bg-ok"}`}
          />
          {paused ? "paused — resume" : "live — pause"}
        </button>

        <div className="flex items-center gap-1.5 bg-bg-1 border border-line-2 rounded px-2 py-1 flex-1 max-w-[240px]">
          <span className="text-ink-3">&#x2315;</span>
          <input
            value={searchQ}
            onChange={(e) => setSearchQ(e.target.value)}
            placeholder="search message / target"
            className="bg-transparent border-none outline-none text-ink font-mono text-xs w-full placeholder:text-ink-4"
          />
        </div>

        <div className="flex gap-1.5">
          <Chip
            active={agentFilter === "all"}
            onClick={() => setAgentFilter("all")}
          >
            all agents
          </Chip>
          {AGENTS.map((a) => (
            <Chip
              key={a}
              active={agentFilter === a}
              onClick={() => setAgentFilter(a)}
            >
              {a}
            </Chip>
          ))}
        </div>

        <div className="ml-auto flex gap-1.5">
          {STATUS_FILTERS.map((s) => (
            <Chip
              key={s}
              active={statusFilter === s}
              onClick={() => setStatusFilter(s)}
            >
              <span className="flex items-center gap-1">
                {s !== "all" && <StatusDot status={s} />}
                {s}
              </span>
            </Chip>
          ))}
        </div>
      </div>

      {/* log table */}
      <div className="flex-1 overflow-y-auto">
        {/* header */}
        <div
          className="grid gap-3 px-4 py-2 border-b border-line text-ink-3 uppercase text-[10px] tracking-wider font-mono sticky top-0 bg-bg z-10"
          style={{ gridTemplateColumns: "90px 110px 130px 1fr 90px" }}
        >
          <div>ts</div>
          <div>agent</div>
          <div>action</div>
          <div>target · message</div>
          <div>status</div>
        </div>

        {logs.length > 0 ? (
          logs.map((l) => <LogRow key={l.id} log={l} />)
        ) : (
          <div className="text-ink-4 font-mono text-xs text-center py-12">
            {paused
              ? "paused — click resume to fetch new logs"
              : "no agent logs yet"}
          </div>
        )}
      </div>
    </div>
  );
}
