import { getRowById, updateRowStatus, recordDecision } from "@/lib/queries";
import { writeBack } from "@/lib/writeback";
import type { DecisionRequest } from "@/lib/types";

export const dynamic = "force-dynamic";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const body = (await request.json()) as DecisionRequest;

  const row = getRowById(id);
  if (!row) {
    return Response.json({ success: false, error: "Row not found" }, { status: 404 });
  }
  if (row.status !== "drafted") {
    return Response.json(
      { success: false, error: `Row status is '${row.status}', expected 'drafted'` },
      { status: 400 }
    );
  }

  const { action, reason = "", notes = "" } = body;
  if (action !== "approve" && action !== "reject") {
    return Response.json(
      { success: false, error: "action must be 'approve' or 'reject'" },
      { status: 400 }
    );
  }

  try {
    // Write to SQLite first
    if (action === "approve") {
      updateRowStatus(id, "queued");
    } else {
      updateRowStatus(id, "closed");
    }
    recordDecision(id, action, reason, notes);

    // Write back to tracker.csv via Python helper
    writeBack(id, action, reason || undefined, notes || undefined);

    const updated = getRowById(id);
    return Response.json({ success: true, row: updated });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "Unknown error";
    return Response.json({ success: false, error: msg }, { status: 500 });
  }
}
