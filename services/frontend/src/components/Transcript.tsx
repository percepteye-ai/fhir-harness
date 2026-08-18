"use client";

import { useState } from "react";
import type { TrajectoryTurn } from "@/lib/types";

function Collapsible({
  label,
  children,
  tone = "tool",
  defaultOpen = false,
}: {
  label: string;
  children: React.ReactNode;
  tone?: "tool" | "think";
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`bubble ${tone === "think" ? "bubble-think" : "bubble-tool"} mt-2`}>
      <button
        className="w-full text-left flex items-center justify-between gap-2 font-mono text-[11px] font-semibold"
        onClick={() => setOpen((o) => !o)}
      >
        <span>{label}</span>
        <span aria-hidden>{open ? "−" : "+"}</span>
      </button>
      {open && <div className="mt-2">{children}</div>}
    </div>
  );
}

export function CodeBlock({ code }: { code: string }) {
  if (!code) return null;
  return <pre className="code-block mt-2">{code}</pre>;
}

export function TurnBlock({ turn }: { turn: TrajectoryTurn }) {
  const hasText = turn.text && turn.text.trim().length > 0;
  const hasThinking = turn.thinking && turn.thinking.trim().length > 0;
  return (
    <div className="fade-in">
      <div className="flex items-center gap-2 mb-1.5">
        <span className="tag font-mono">turn {turn.turn + 1}</span>
        {turn.tool_calls.map((tc, i) => (
          <span key={i} className="tag tag-accent font-mono">
            {tc}
          </span>
        ))}
      </div>

      {hasThinking && (
        <Collapsible label="reasoning" tone="think">
          <div className="whitespace-pre-wrap text-[12.5px]">{turn.thinking}</div>
        </Collapsible>
      )}

      {hasText && (
        <div className="bubble bubble-assistant whitespace-pre-wrap">{turn.text}</div>
      )}

      {turn.code ? <CodeBlock code={turn.code} /> : null}

      {turn.tool_results.map((r, i) => (
        <Collapsible key={i} label={`tool result ${turn.tool_results.length > 1 ? `#${i + 1}` : ""}`}>
          <pre className="code-block" style={{ background: "#0b1f1d" }}>
            {r}
          </pre>
        </Collapsible>
      ))}
    </div>
  );
}
