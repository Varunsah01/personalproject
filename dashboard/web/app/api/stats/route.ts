import { getFunnelCounts, getLastSync, getStaleCount } from "@/lib/queries";

export const dynamic = "force-dynamic";

export async function GET() {
  const funnel = getFunnelCounts();
  const lastSync = getLastSync();
  const staleCount = getStaleCount();
  const draftsPending = funnel["drafted"] || 0;

  return Response.json({ funnel, lastSync, draftsPending, staleCount });
}
