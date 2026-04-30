"use client";

import TopNav from "@/components/TopNav";
import { usePolling } from "@/hooks/usePolling";
import type {
  AnalyticsResponse,
  FunnelBar,
  TierReplyRate,
  HookSource,
  InboxUsage,
} from "@/lib/types";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";

// Short labels for funnel chart
const STATUS_SHORT: Record<string, string> = {
  research_done: "research",
  people_found: "people",
  contact_found: "contact",
  linkedin_queue: "linkedin",
  drafted: "drafted",
  queued: "queued",
  sent: "sent",
  replied: "replied",
  closed: "closed",
};

// Bar color gets brighter as rows advance
const BAR_COLORS: Record<string, string> = {
  research_done: "#4a4b53",
  people_found: "#5a5b63",
  contact_found: "#6e6f78",
  linkedin_queue: "#6e6f78",
  drafted: "#8a8b93",
  queued: "#a8a9b0",
  sent: "#c8c8d0",
  replied: "#e8e8ea",
  closed: "#6e6f78",
};

function FunnelCard({ data }: { data: FunnelBar[] }) {
  return (
    <div className="bg-bg-1 border border-line rounded-[5px] p-4 flex flex-col gap-3">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[13px] text-ink">funnel</span>
        <span className="font-mono text-[11px] text-ink-3">
          {"\u00B7"} last 7 days
        </span>
      </div>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            margin={{ top: 4, right: 4, bottom: 0, left: -20 }}
          >
            <XAxis
              dataKey="status"
              tickFormatter={(v: string) => STATUS_SHORT[v] || v}
              tick={{ fill: "#6e6f78", fontSize: 10, fontFamily: "var(--font-mono)" }}
              axisLine={{ stroke: "#25272e" }}
              tickLine={false}
            />
            <YAxis
              tick={{ fill: "#6e6f78", fontSize: 10, fontFamily: "var(--font-mono)" }}
              axisLine={false}
              tickLine={false}
              allowDecimals={false}
            />
            <Tooltip
              contentStyle={{
                background: "#181a1f",
                border: "1px solid #2e313a",
                borderRadius: 4,
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                color: "#e8e8ea",
              }}
              labelFormatter={(v) => STATUS_SHORT[String(v)] || String(v)}
              cursor={{ fill: "rgba(255,255,255,0.03)" }}
            />
            <Bar dataKey="count" radius={[3, 3, 0, 0]}>
              {data.map((entry) => (
                <Cell
                  key={entry.status}
                  fill={BAR_COLORS[entry.status] || "#6e6f78"}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function ReplyRateCard({ data }: { data: TierReplyRate[] }) {
  return (
    <div className="bg-bg-1 border border-line rounded-[5px] p-4 flex flex-col gap-3">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[13px] text-ink">reply rate</span>
        <span className="font-mono text-[11px] text-ink-3">
          {"\u00B7"} by tier, 30 days
        </span>
      </div>
      {data.length === 0 ? (
        <div className="text-ink-3 font-mono text-xs py-4 text-center">
          no sent rows in window
        </div>
      ) : (
        <table className="w-full font-mono text-xs">
          <thead>
            <tr className="text-ink-3 text-[10px] uppercase tracking-wider">
              <th className="text-left py-1.5">tier</th>
              <th className="text-right py-1.5">sent</th>
              <th className="text-right py-1.5">replied</th>
              <th className="text-right py-1.5">rate</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row) => (
              <tr key={row.tier} className="border-t border-line">
                <td className="py-1.5 text-ink">{row.tier}</td>
                <td className="py-1.5 text-right text-ink-2">{row.sent}</td>
                <td className="py-1.5 text-right text-ink-2">{row.replied}</td>
                <td className="py-1.5 text-right text-ink">
                  {row.sent > 0
                    ? `${Math.round(row.rate * 100)}%`
                    : "\u2014"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function TopHooksCard({ data }: { data: HookSource[] }) {
  return (
    <div className="bg-bg-1 border border-line rounded-[5px] p-4 flex flex-col gap-3">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[13px] text-ink">top hooks</span>
        <span className="font-mono text-[11px] text-ink-3">
          {"\u00B7"} by source domain
        </span>
      </div>
      {data.length === 0 ? (
        <div className="text-ink-3 font-mono text-xs py-4 text-center">
          no hook sources found
        </div>
      ) : (
        <table className="w-full font-mono text-xs">
          <thead>
            <tr className="text-ink-3 text-[10px] uppercase tracking-wider">
              <th className="text-left py-1.5">domain</th>
              <th className="text-right py-1.5">used</th>
              <th className="text-right py-1.5">replied</th>
              <th className="text-right py-1.5">rate</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row) => (
              <tr key={row.domain} className="border-t border-line">
                <td className="py-1.5 text-ink truncate max-w-[180px]">
                  {row.domain}
                </td>
                <td className="py-1.5 text-right text-ink-2">{row.count}</td>
                <td className="py-1.5 text-right text-ink-2">{row.replied}</td>
                <td className="py-1.5 text-right text-ink">
                  {row.count > 0
                    ? `${Math.round(row.rate * 100)}%`
                    : "\u2014"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function InboxUsageCard({ data }: { data: InboxUsage[] }) {
  return (
    <div className="bg-bg-1 border border-line rounded-[5px] p-4 flex flex-col gap-3">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[13px] text-ink">inbox usage</span>
        <span className="font-mono text-[11px] text-ink-3">
          {"\u00B7"} today
        </span>
      </div>
      {data.length === 0 ? (
        <div className="text-ink-3 font-mono text-xs py-4 text-center">
          no sends today
        </div>
      ) : (
        <div className="flex flex-col gap-2.5">
          {data.map((row) => {
            const pct = Math.min((row.sent / row.cap) * 100, 100);
            const warn = pct >= 80;
            return (
              <div key={row.inbox} className="flex flex-col gap-1">
                <div className="flex items-center justify-between font-mono text-[11px]">
                  <span className="text-ink-2 truncate max-w-[200px]">
                    {row.inbox}
                  </span>
                  <span className={warn ? "text-warn" : "text-ink-3"}>
                    {row.sent}/{row.cap}
                  </span>
                </div>
                <div className="h-2 bg-bg-3 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full ${
                      warn ? "bg-warn" : "bg-ink-3"
                    }`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function AnalyticsPage() {
  const { data } = usePolling<AnalyticsResponse>("/api/analytics", 15_000);

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

  return (
    <div className="h-screen flex flex-col">
      <TopNav />
      <div className="flex-1 overflow-y-auto p-4">
        <div className="max-w-4xl mx-auto grid grid-cols-1 md:grid-cols-2 gap-4">
          <FunnelCard data={data.funnel7d} />
          <ReplyRateCard data={data.replyRate} />
          <TopHooksCard data={data.topHooks} />
          <InboxUsageCard data={data.inboxUsage} />
        </div>
      </div>
    </div>
  );
}
