import { getDraftedRows } from "@/lib/queries";

export const dynamic = "force-dynamic";

export async function GET() {
  const drafts = getDraftedRows();
  return Response.json({ drafts });
}
