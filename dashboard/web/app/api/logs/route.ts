import { type NextRequest } from "next/server";
import { getAgentLogs } from "@/lib/queries";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const sp = request.nextUrl.searchParams;
  const agent = sp.get("agent") || undefined;
  const status = sp.get("status") || undefined;
  const q = sp.get("q") || undefined;
  const limit = sp.get("limit") ? parseInt(sp.get("limit")!, 10) : 200;

  const { logs, total } = getAgentLogs({ agent, status, q, limit });
  return Response.json({ logs, total });
}
