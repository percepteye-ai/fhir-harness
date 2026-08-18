export interface TrajectoryTurn {
  turn: number;
  text: string;
  thinking: string;
  tool_calls: string[];
  code: string;
  tool_results: string[];
}

export interface RunEvent {
  type:
    | "queued"
    | "preparing"
    | "start"
    | "system_prompt"
    | "assistant"
    | "code"
    | "tool_result"
    | "nudge"
    | "terminated"
    | "done"
    | "error";
  message?: string;
  rollout_id?: string;
  agent_mode?: string;
  model?: string;
  max_turns?: number;
  system_prompt?: string;
  instruction?: string;
  turn?: number;
  text?: string;
  thinking?: string;
  tool_calls?: string[];
  finish_reason?: string;
  code?: string;
  tool?: string;
  result?: string;
  reason?: string;
  turns?: number;
}

export interface PromptsResponse {
  agent_mode: string;
  system_prompt: string;
  instruction: string;
}
