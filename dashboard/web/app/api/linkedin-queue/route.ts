import { getLinkedInQueueRows } from "@/lib/queries";

export const dynamic = "force-dynamic";

export async function GET() {
  const rows = getLinkedInQueueRows();
  return Response.json({ rows });
}
