import { getRowById, updateRowStatus, recordDecision } from "@/lib/queries";
import { writeBack } from "@/lib/writeback";

export const dynamic = "force-dynamic";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const body = (await request.json()) as { notes?: string };

  const row = getRowById(id);
  if (!row) {
    return Response.json({ success: false, error: "Row not found" }, { status: 404 });
  }
  if (row.status !== "linkedin_queue") {
    return Response.json(
      { success: false, error: `Row status is '${row.status}', expected 'linkedin_queue'` },
      { status: 400 }
    );
  }

  try {
    updateRowStatus(id, "closed");
    recordDecision(id, "linkedin_messaged", "", body.notes || "");

    writeBack(id, "linkedin_messaged", undefined, body.notes || undefined);

    const updated = getRowById(id);
    return Response.json({ success: true, row: updated });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "Unknown error";
    return Response.json({ success: false, error: msg }, { status: 500 });
  }
}
