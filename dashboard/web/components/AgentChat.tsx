"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import type { OutreachRow, ChatMessage } from "@/lib/types";

export default function AgentChat({ row }: { row: OutreachRow }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to bottom
  const scrollToBottom = useCallback(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, []);

  useEffect(scrollToBottom, [messages, scrollToBottom]);

  // Load history on mount / row change
  useEffect(() => {
    setMessages([]);
    setLoadError(null);
    fetch(`/api/chat?row_id=${row.id}`)
      .then((res) => res.json())
      .then((data) => {
        if (data.messages?.length) setMessages(data.messages);
      })
      .catch(() => setLoadError("Failed to load chat history"));
  }, [row.id]);

  // Focus textarea on mount
  useEffect(() => {
    textareaRef.current?.focus();
  }, [row.id]);

  const sendMessage = useCallback(async () => {
    const text = input.trim();
    if (!text || isStreaming) return;

    setInput("");
    setIsStreaming(true);

    const userMsg: ChatMessage = {
      ts: new Date().toISOString(),
      role: "user",
      content: text,
      agent: null,
    };
    const placeholderMsg: ChatMessage = {
      ts: new Date().toISOString(),
      role: "assistant",
      content: "",
      agent: null,
    };

    setMessages((prev) => [...prev, userMsg, placeholderMsg]);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ row_id: row.id, message: text }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: "Unknown error" }));
        setMessages((prev) => {
          const updated = [...prev];
          updated[updated.length - 1] = {
            ...updated[updated.length - 1],
            content: `[error: ${err.error || res.statusText}]`,
          };
          return updated;
        });
        setIsStreaming(false);
        return;
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let accumulated = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        accumulated += decoder.decode(value, { stream: true });
        const snapshot = accumulated;
        setMessages((prev) => {
          const updated = [...prev];
          updated[updated.length - 1] = {
            ...updated[updated.length - 1],
            content: snapshot,
          };
          return updated;
        });
      }
    } catch {
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = {
          ...updated[updated.length - 1],
          content: "[error: connection failed]",
        };
        return updated;
      });
    } finally {
      setIsStreaming(false);
      textareaRef.current?.focus();
    }
  }, [input, isStreaming, row.id]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* message list */}
      <div ref={listRef} className="flex-1 overflow-y-auto p-3 flex flex-col gap-2">
        {loadError && (
          <div className="text-err font-mono text-[11px] text-center py-2">
            {loadError}
          </div>
        )}
        {messages.length === 0 && !loadError && (
          <div className="text-ink-4 font-mono text-[11px] text-center py-8">
            ask a question about this row
          </div>
        )}
        {messages.map((msg, i) => (
          <MessageBubble key={i} msg={msg} isLast={i === messages.length - 1} isStreaming={isStreaming} />
        ))}
      </div>

      {/* input bar */}
      <div className="border-t border-line p-2 flex gap-2 items-end shrink-0">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isStreaming}
          placeholder={isStreaming ? "waiting..." : "ask about this row..."}
          rows={1}
          className="flex-1 bg-bg-2 border border-line-2 rounded px-2.5 py-1.5 text-ink font-mono text-xs resize-none outline-none placeholder:text-ink-4 disabled:opacity-50"
          style={{ minHeight: "32px", maxHeight: "96px" }}
        />
        <button
          onClick={sendMessage}
          disabled={isStreaming || !input.trim()}
          className="font-mono text-xs border border-line-2 rounded px-2.5 py-1.5 text-ink hover:border-ink-3 cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed shrink-0"
        >
          send
        </button>
      </div>
    </div>
  );
}

function MessageBubble({
  msg,
  isLast,
  isStreaming,
}: {
  msg: ChatMessage;
  isLast: boolean;
  isStreaming: boolean;
}) {
  const isUser = msg.role === "user";
  const showCursor = isLast && isStreaming && !isUser;

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[90%] rounded px-2.5 py-1.5 text-xs whitespace-pre-wrap ${
          isUser
            ? "bg-bg-3 border border-line-2 text-ink"
            : "bg-bg-2 border border-line text-ink-2"
        }`}
      >
        {!isUser && msg.agent && (
          <div className="font-mono text-[10px] text-ink-4 mb-1">
            {msg.agent}
          </div>
        )}
        <div>
          {msg.content || (showCursor ? "" : "")}
          {showCursor && (
            <span className="inline-block w-1.5 h-3 bg-ink-3 ml-0.5 animate-pulse" />
          )}
        </div>
      </div>
    </div>
  );
}
