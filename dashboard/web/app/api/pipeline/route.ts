import { getPipelineRows, getFunnelCounts, getLastSync } from "@/lib/queries";

export const dynamic = "force-dynamic";

export async function GET() {
  const columns = getPipelineRows();
  const counts = getFunnelCounts();
  const lastSync = getLastSync();

  return Response.json({ columns, counts, lastSync });
}
