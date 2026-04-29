import fs from "fs";
import path from "path";
import { getDb, PROJECT_ROOT } from "./db";
import type { OutreachRow, DraftRow, AgentLog } from "./types";
import { PIPELINE_STATUSES } from "./types";

function computeStaleness(row: OutreachRow): OutreachRow {
  const updated = row.last_updated ? new Date(row.last_updated).getTime() : 0;
  const days = updated
    ? Math.floor((Date.now() - updated) / 86_400_000)
    : 0;
  return { ...row, days_in_column: days, stale: days >= 5 };
}

function parseManagerScore(notes: string): number | null {
  // Format: quality: S/V/A/L/R avg=X.X
  const match = notes.match(/avg=(\d+(?:\.\d+)?)/);
  if (!match) return null;
  return Math.round(parseFloat(match[1]) * 20); // 1-5 → 20-100
}

export function getPipelineRows(): Record<string, OutreachRow[]> {
  const db = getDb();
  const rows = db
    .prepare(
      `SELECT * FROM outreach_rows WHERE status != 'closed' ORDER BY last_updated DESC`
    )
    .all() as OutreachRow[];

  const columns: Record<string, OutreachRow[]> = {};
  for (const status of PIPELINE_STATUSES) {
    columns[status] = [];
  }
  for (const row of rows) {
    const enriched = computeStaleness(row);
    if (columns[enriched.status]) {
      columns[enriched.status].push(enriched);
    }
  }
  return columns;
}

export function getFunnelCounts(): Record<string, number> {
  const db = getDb();
  const rows = db
    .prepare(`SELECT status, COUNT(*) as count FROM outreach_rows GROUP BY status`)
    .all() as { status: string; count: number }[];
  const counts: Record<string, number> = {};
  for (const r of rows) {
    counts[r.status] = r.count;
  }
  return counts;
}

export function getLastSync(): string {
  const db = getDb();
  const row = db
    .prepare(`SELECT MAX(synced_at) as last_sync FROM outreach_rows`)
    .get() as { last_sync: string } | undefined;
  return row?.last_sync || "";
}

export function getStaleCount(): number {
  const db = getDb();
  const cutoff = new Date(Date.now() - 5 * 86_400_000).toISOString();
  const row = db
    .prepare(
      `SELECT COUNT(*) as n FROM outreach_rows WHERE status != 'closed' AND last_updated < ? AND last_updated != ''`
    )
    .get(cutoff) as { n: number };
  return row.n;
}

export function getDraftedRows(): DraftRow[] {
  const db = getDb();
  const rows = db
    .prepare(
      `SELECT * FROM outreach_rows WHERE status = 'drafted' ORDER BY role_tier ASC, last_updated DESC`
    )
    .all() as OutreachRow[];

  return rows.map((row) => {
    let bodyText = "";
    if (row.body_path) {
      const fullPath = path.resolve(PROJECT_ROOT, row.body_path);
      try {
        bodyText = fs.readFileSync(fullPath, "utf-8");
      } catch {
        bodyText = "[ draft file not found ]";
      }
    }
    return {
      ...computeStaleness(row),
      body_text: bodyText,
      manager_score: parseManagerScore(row.notes),
    };
  });
}

export function getRowById(id: string): OutreachRow | null {
  const db = getDb();
  const row = db
    .prepare(`SELECT * FROM outreach_rows WHERE id = ?`)
    .get(id) as OutreachRow | undefined;
  return row ? computeStaleness(row) : null;
}

export function updateRowStatus(id: string, status: string): void {
  const db = getDb();
  db.prepare(
    `UPDATE outreach_rows SET status = ?, last_updated = datetime('now') WHERE id = ?`
  ).run(status, id);
}

export function recordDecision(
  rowId: string,
  action: "approve" | "reject",
  reason: string,
  notes: string
): void {
  const db = getDb();
  db.prepare(
    `INSERT INTO manager_decisions (row_id, action, reason, notes) VALUES (?, ?, ?, ?)`
  ).run(rowId, action, reason, notes);
}

export function getAgentLogs(filters: {
  agent?: string;
  status?: string;
  q?: string;
  limit?: number;
}): { logs: AgentLog[]; total: number } {
  const db = getDb();
  const conditions: string[] = [];
  const params: (string | number)[] = [];

  if (filters.agent) {
    conditions.push("agent = ?");
    params.push(filters.agent);
  }
  if (filters.status) {
    conditions.push("status = ?");
    params.push(filters.status);
  }
  if (filters.q) {
    conditions.push("(message LIKE ? OR target LIKE ? OR action LIKE ?)");
    const q = `%${filters.q}%`;
    params.push(q, q, q);
  }

  const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
  const limit = filters.limit || 200;

  const logs = db
    .prepare(`SELECT * FROM agent_logs ${where} ORDER BY timestamp DESC LIMIT ?`)
    .all(...params, limit) as AgentLog[];

  const totalRow = db
    .prepare(`SELECT COUNT(*) as n FROM agent_logs ${where}`)
    .get(...params) as { n: number };

  return { logs, total: totalRow.n };
}
