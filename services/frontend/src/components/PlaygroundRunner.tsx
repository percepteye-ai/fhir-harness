"use client";

import { useEffect, useRef, useState } from "react";
import { apiUrl, getJSON } from "@/lib/api";
import type { PromptsResponse, RunEvent, TrajectoryTurn } from "@/lib/types";
import { TurnBlock } from "./Transcript";

type AgentMode = "code_exec_only" | "hybrid" | "tools_only";

export default function PlaygroundRunner() {
  const [systemPrompt, setSystemPrompt] = useState("");
  const [instruction, setInstruction] = useState("");
  const [agentMode, setAgentMode] = useState<AgentMode>("code_exec_only");
  const [toolsYaml, setToolsYaml] = useState("");
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState("");
  const [header, setHeader] = useState<{ model?: string }>({});
  const [turns, setTurns] = useState<Record<number, TrajectoryTurn>>({});
  const [terminated, setTerminated] = useState<{ reason: string; turns: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [temperature, setTemperature] = useState(1.0);
  const [maxTurns, setMaxTurns] = useState(30);
  const [thinking, setThinking] = useState(true);

  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    getJSON<PromptsResponse>(`/api/prompts?agent_mode=${agentMode}`)
      .then((d) => {
        setSystemPrompt(d.system_prompt);
        if (!instruction) setInstruction(d.instruction || "");
      })
      .catch((e) => setError(`Cannot reach backend: ${e.message || e}`));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentMode]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, status]);

  function reset() {
    setStatus("");
    setHeader({});
    setTurns({});
    setTerminated(null);
    setError(null);
  }

  function upsertTurn(turn: number, patch: Partial<TrajectoryTurn>) {
    setTurns((prev) => {
      const cur: TrajectoryTurn =
        prev[turn] || { turn, text: "", thinking: "", tool_calls: [], code: "", tool_results: [] };
      return { ...prev, [turn]: { ...cur, ...patch } };
    });
  }

  function appendToolResult(turn: number, result: string) {
    setTurns((prev) => {
      const cur: TrajectoryTurn =
        prev[turn] || { turn, text: "", thinking: "", tool_calls: [], code: "", tool_results: [] };
      return { ...prev, [turn]: { ...cur, tool_results: [...cur.tool_results, result] } };
    });
  }

  function handleEvent(ev: RunEvent) {
    switch (ev.type) {
      case "queued":
      case "preparing":
        setStatus(ev.message || "");
        break;
      case "start":
        setHeader({ model: ev.model });
        setStatus(`running · ${ev.model || ""}`);
        break;
      case "system_prompt":
        break;
      case "assistant":
        upsertTurn(ev.turn ?? 0, {
          text: ev.text || "",
          thinking: ev.thinking || "",
          tool_calls: ev.tool_calls || [],
        });
        break;
      case "code":
        upsertTurn(ev.turn ?? 0, { code: ev.code || "" });
        break;
      case "tool_result":
        appendToolResult(ev.turn ?? 0, ev.result || "");
        break;
      case "nudge":
        setStatus(ev.message || "final-turn nudge sent");
        break;
      case "terminated":
        setTerminated({ reason: ev.reason || "", turns: ev.turns || 0 });
        setStatus("");
        break;
      case "done":
        setRunning(false);
        setStatus("");
        break;
      case "error":
        setError(ev.message || "unknown error");
        break;
    }
  }

  async function run() {
    if (running) return;
    if (!instruction.trim()) {
      setError("Paste a user instruction (the clinical task), then click Run. The system prompt alone is not enough.");
      return;
    }
    reset();
    setRunning(true);
    setStatus("connecting…");
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const res = await fetch(apiUrl("/api/playground/run"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          instruction,
          system_prompt: systemPrompt,
          agent_mode: agentMode,
          tools_yaml: toolsYaml.trim() || undefined,
          temperature,
          max_main_turns: maxTurns,
          enable_thinking: thinking,
        }),
        signal: ctrl.signal,
      });
      if (!res.ok || !res.body) throw new Error(`${res.status} ${res.statusText}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const dataLine = chunk.split("\n").find((l) => l.startsWith("data:"));
          if (!dataLine) continue;
          try {
            handleEvent(JSON.parse(dataLine.slice(5).trim()) as RunEvent);
          } catch {
            /* ignore malformed chunk */
          }
        }
      }
    } catch (e: unknown) {
      if ((e as Error).name !== "AbortError") setError(String((e as Error).message || e));
    } finally {
      setRunning(false);
      abortRef.current = null;
    }
  }

  function stop() {
    abortRef.current?.abort();
    setRunning(false);
    setStatus("stopped");
  }

  const orderedTurns = Object.values(turns).sort((a, b) => a.turn - b.turn);

  return (
    <div className="grid lg:grid-cols-[380px_1fr] gap-6">
      <div className="space-y-4">
        <div className="clinical-card p-4 space-y-3">
          <div>
            <label className="label">Agent mode</label>
            <select
              className="mt-1 w-full bg-[var(--paper-sunken)] border border-[var(--rule)] rounded-[10px] px-3 py-2 text-[13px]"
              value={agentMode}
              onChange={(e) => setAgentMode(e.target.value as AgentMode)}
              disabled={running}
            >
              <option value="code_exec_only">code_exec_only</option>
              <option value="hybrid">hybrid</option>
              <option value="tools_only">tools_only</option>
            </select>
          </div>

          <div>
            <label className="label" htmlFor="pe-system-prompt">System prompt</label>
            <textarea
              id="pe-system-prompt"
              className="mt-1 w-full bg-[var(--paper-sunken)] border border-[var(--rule)] rounded-[10px] px-3 py-2 text-[13px] leading-relaxed resize-y min-h-[160px] font-mono"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              disabled={running}
              placeholder="System prompt sent as the first message…"
            />
          </div>

          <div>
            <label className="label" htmlFor="pe-instruction">User instruction</label>
            <textarea
              id="pe-instruction"
              className="mt-1 w-full bg-[var(--paper-sunken)] border border-[var(--rule)] rounded-[10px] px-3 py-2 text-[13px] leading-relaxed resize-y min-h-[140px] font-mono"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              disabled={running}
              placeholder="Clinical instruction — this is the user prompt."
            />
          </div>

          <div>
            <label className="label">Tools YAML (optional override)</label>
            <textarea
              className="mt-1 w-full bg-[var(--paper-sunken)] border border-[var(--rule)] rounded-[10px] px-3 py-2 text-[12px] leading-relaxed resize-y min-h-[80px] font-mono"
              value={toolsYaml}
              onChange={(e) => setToolsYaml(e.target.value)}
              disabled={running}
              placeholder="Paste a tools.yaml to override the default list for this run…"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">Temperature</label>
              <input
                type="number"
                step="0.1"
                min="0"
                max="2"
                className="mt-1 w-full bg-[var(--paper-sunken)] border border-[var(--rule)] rounded-[10px] px-3 py-2 text-[13px] tnum"
                value={temperature}
                onChange={(e) => setTemperature(parseFloat(e.target.value))}
                disabled={running}
              />
            </div>
            <div>
              <label className="label">Max turns</label>
              <input
                type="number"
                min="1"
                max="60"
                className="mt-1 w-full bg-[var(--paper-sunken)] border border-[var(--rule)] rounded-[10px] px-3 py-2 text-[13px] tnum"
                value={maxTurns}
                onChange={(e) => setMaxTurns(parseInt(e.target.value || "30", 10))}
                disabled={running}
              />
            </div>
          </div>

          <label className="flex items-center gap-2 text-[13px] text-[var(--ink-soft)]">
            <input
              type="checkbox"
              checked={thinking}
              onChange={(e) => setThinking(e.target.checked)}
              disabled={running}
            />
            Enable thinking
          </label>

          <div className="flex gap-2 pt-1">
            <button
              className="btn-primary flex-1 justify-center"
              onClick={run}
              disabled={running}
            >
              {running ? "Running…" : "Run"}
            </button>
            {running && (
              <button className="btn-ghost" onClick={stop}>
                Stop
              </button>
            )}
          </div>
        </div>

        <div className="clinical-card p-4">
          <div className="label mb-1">How this works</div>
          <p className="text-[12.5px] text-[var(--ink-soft)] leading-relaxed">
            System and user prompts are sent on every Run — edit them live, no restart.
            Tools come from YAML (default <span className="font-mono">examples/tools.yaml</span>).
            The library talks to your Open FHIR or AWS HealthLake server.
          </p>
        </div>
      </div>

      <div className="space-y-4">
        {(status || running) && (
          <div className="clinical-card p-3 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full pulse-dot" style={{ background: "var(--coral)" }} />
            <span className="text-[13px] text-[var(--ink-soft)]">{status || "working…"}</span>
            {header.model && (
              <span className="ml-auto font-mono text-[11px] text-[var(--ink-faint)]">
                {header.model}
              </span>
            )}
          </div>
        )}

        {error && (
          <div className="clinical-card p-4">
            <div className="font-semibold text-[var(--bad)] mb-1">Error</div>
            <p className="text-[13px] text-[var(--ink-soft)] whitespace-pre-wrap">{error}</p>
          </div>
        )}

        {terminated && (
          <div className="clinical-card p-3 font-mono text-[12px] text-[var(--ink-soft)]">
            ended: {terminated.reason} · {terminated.turns} turns
          </div>
        )}

        <div className="space-y-4">
          {orderedTurns.map((t) => (
            <TurnBlock key={t.turn} turn={t} />
          ))}
        </div>

        {!running && orderedTurns.length === 0 && !error && (
          <div className="text-[var(--ink-faint)] text-[14px]">
            Paste an instruction and hit <span className="font-semibold">Run</span> to watch the
            agent work in real time.
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
