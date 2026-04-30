import { spawn, execSync } from "child_process";
import { NextRequest } from "next/server";
import fs from "fs";
import path from "path";
import crypto from "crypto";
import { PROJECT_ROOT } from "@/lib/db";

export const dynamic = "force-dynamic";

const CHAT_DIR = path.join(PROJECT_ROOT, "outreach/data/chat");
const CHAT_SCRIPT = path.join(PROJECT_ROOT, "outreach/chat.py");
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function chatFilePath(rowId: string): string {
  return path.join(CHAT_DIR, `${rowId}.jsonl`);
}

interface ChatLine {
  ts: string;
  role: "user" | "assistant";
  content: string;
  agent: string | null;
}

function readHistory(rowId: string): ChatLine[] {
  const fp = chatFilePath(rowId);
  if (!fs.existsSync(fp)) return [];
  try {
    const lines = fs.readFileSync(fp, "utf-8").trim().split("\n").filter(Boolean);
    return lines.map((l) => JSON.parse(l) as ChatLine);
  } catch {
    return [];
  }
}

function appendLine(rowId: string, line: ChatLine): void {
  if (!fs.existsSync(CHAT_DIR)) {
    fs.mkdirSync(CHAT_DIR, { recursive: true });
  }
  fs.appendFileSync(chatFilePath(rowId), JSON.stringify(line) + "\n");
}

// ── GET: load chat history ──────────────────────────────────────────────

export async function GET(req: NextRequest) {
  const rowId = req.nextUrl.searchParams.get("row_id");
  if (!rowId || !UUID_RE.test(rowId)) {
    return Response.json({ error: "invalid row_id" }, { status: 400 });
  }
  return Response.json({ messages: readHistory(rowId) });
}

// ── POST: stream chat response ──────────────────────────────────────────

export async function POST(req: NextRequest) {
  const body = await req.json();
  const { row_id: rowId, message } = body as {
    row_id: string;
    message: string;
  };

  if (!rowId || !UUID_RE.test(rowId)) {
    return Response.json({ error: "invalid row_id" }, { status: 400 });
  }
  if (!message || typeof message !== "string" || !message.trim()) {
    return Response.json({ error: "message is required" }, { status: 400 });
  }

  // Check claude is on PATH
  try {
    execSync("which claude", { stdio: "ignore" });
  } catch {
    return Response.json(
      { error: "claude CLI not found on PATH" },
      { status: 503 },
    );
  }

  // Persist user message immediately
  const userLine: ChatLine = {
    ts: new Date().toISOString(),
    role: "user",
    content: message.trim(),
    agent: null,
  };
  appendLine(rowId, userLine);

  // Write recent history to temp file for context
  const history = readHistory(rowId);
  const recentHistory = history.slice(-20);
  const tmpId = crypto.randomUUID();
  const tmpFile = path.join("/tmp", `chat-history-${tmpId}.json`);
  fs.writeFileSync(tmpFile, JSON.stringify(recentHistory));

  // Spawn python chat.py
  const child = spawn(
    "python3",
    [CHAT_SCRIPT, "--row-id", rowId, "--history-file", tmpFile],
    { cwd: PROJECT_ROOT, stdio: ["pipe", "pipe", "pipe"] },
  );

  // Write message to stdin and close
  child.stdin.write(message.trim());
  child.stdin.end();

  let agentName: string | null = null;
  let fullResponse = "";
  let isFirstChunk = true;
  let leftover = "";

  const stream = new ReadableStream({
    start(controller) {
      const encoder = new TextEncoder();

      child.stdout.on("data", (data: Buffer) => {
        let text = leftover + data.toString("utf-8");
        leftover = "";

        // Parse the __agent__ metadata from the first line
        if (isFirstChunk) {
          const nlIdx = text.indexOf("\n");
          if (nlIdx === -1) {
            // Haven't received full first line yet, buffer it
            leftover = text;
            return;
          }
          const firstLine = text.slice(0, nlIdx);
          if (firstLine.startsWith("__agent__:")) {
            agentName = firstLine.slice("__agent__:".length).trim();
          }
          text = text.slice(nlIdx + 1);
          isFirstChunk = false;
        }

        if (text) {
          fullResponse += text;
          controller.enqueue(encoder.encode(text));
        }
      });

      child.stderr.on("data", (data: Buffer) => {
        // Log stderr but don't send to client
        const errText = data.toString("utf-8");
        if (errText.trim()) {
          console.error("[chat.py stderr]", errText.trim());
        }
      });

      child.on("close", (code) => {
        // Handle any leftover data (shouldn't happen normally)
        if (leftover) {
          fullResponse += leftover;
          controller.enqueue(encoder.encode(leftover));
        }

        // Persist assistant response
        if (fullResponse.trim()) {
          const assistantLine: ChatLine = {
            ts: new Date().toISOString(),
            role: "assistant",
            content: fullResponse.trim(),
            agent: agentName,
          };
          appendLine(rowId, assistantLine);
        }

        // Cleanup temp file
        try {
          fs.unlinkSync(tmpFile);
        } catch {
          // ignore
        }

        if (code !== 0 && !fullResponse.trim()) {
          controller.enqueue(
            encoder.encode("[error: chat agent exited unexpectedly]"),
          );
        }

        controller.close();
      });

      child.on("error", (err) => {
        controller.enqueue(
          encoder.encode(`[error: ${err.message}]`),
        );
        controller.close();
      });
    },

    cancel() {
      child.kill("SIGTERM");
      try {
        fs.unlinkSync(tmpFile);
      } catch {
        // ignore
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "no-cache, no-store",
      "Transfer-Encoding": "chunked",
    },
  });
}
