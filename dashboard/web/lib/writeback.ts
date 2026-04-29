import { execSync } from "child_process";
import path from "path";

const PROJECT_ROOT = path.resolve(process.cwd(), "../../");
const WRITEBACK_SCRIPT = path.join(
  PROJECT_ROOT,
  "outreach/lib/writeback.py"
);

export function writeBack(
  rowId: string,
  action: "approve" | "reject",
  reason?: string,
  notes?: string
): string {
  const args = [
    "python3",
    WRITEBACK_SCRIPT,
    "--row-id",
    rowId,
    "--action",
    action,
  ];
  if (reason) args.push("--reason", reason);
  if (notes) args.push("--notes", notes);

  const result = execSync(args.join(" "), {
    cwd: PROJECT_ROOT,
    timeout: 10_000,
    encoding: "utf-8",
  });
  return result.trim();
}
