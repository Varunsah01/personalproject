const dotColors: Record<string, string> = {
  ok: "bg-ok",
  warn: "bg-warn",
  error: "bg-err",
};

export default function StatusDot({ status }: { status: string }) {
  return (
    <span
      className={`inline-block w-1.5 h-1.5 rounded-full ${dotColors[status] || "bg-ink-4"}`}
    />
  );
}
