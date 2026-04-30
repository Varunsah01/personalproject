import {
  getFunnelLast7Days,
  getReplyRateByTier,
  getTopHookSources,
  getInboxUsageToday,
} from "@/lib/queries";

export const dynamic = "force-dynamic";

export async function GET() {
  return Response.json({
    funnel7d: getFunnelLast7Days(),
    replyRate: getReplyRateByTier(),
    topHooks: getTopHookSources(),
    inboxUsage: getInboxUsageToday(),
  });
}
