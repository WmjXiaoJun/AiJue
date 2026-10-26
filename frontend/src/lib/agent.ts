// V6 Agent API 前端层：类型 + fetch 封装（后端 backend/app/routers/v6_agent.py）
import { API_BASE, mediaUrl } from "./api";

export interface AgentRole {
  id: string;
  name: string;
  emoji?: string;
  desc?: string;
  tools?: string[];
}

export interface AgentStage {
  id: string;
  agent?: string;
  tool?: string;
  depends?: string[];
  enabled?: boolean;
  optional?: boolean;
  params?: Record<string, unknown>;
  desc?: string;
}

export interface AgentTemplate {
  id: string;
  label?: string;
  style?: string;
  cast?: (string | Record<string, unknown>)[];
  stages: AgentStage[];
  workflow_file: string;
}

export interface AgentOverview {
  ok: boolean;
  roles: AgentRole[];
  templates: AgentTemplate[];
  count: number;
  runs: string[];
}

export interface AgentWorkflow {
  id: string;
  name?: string;
  version?: number;
  variables?: Record<string, unknown>;
  stages: AgentStage[];
  [key: string]: unknown;
}

export interface AgentJobLog {
  t?: string;
  stage?: string;
  msg?: string;
}

export interface AgentJobStage {
  id: string;
  agent?: string;
  tool?: string;
  status: string;
  elapsed_sec?: number;
  result?: unknown;
  error?: string;
}

export interface AgentJob {
  job_id: string;
  status: string;
  created?: string;
  started?: string;
  ended?: string;
  error?: string;
  logs?: AgentJobLog[];
  run_dir?: string;
  result?: {
    run_id?: string;
    status?: string;
    stages?: AgentJobStage[];
    outputs?: Record<string, unknown>;
  };
}

export interface AgentEditResult {
  ok?: boolean;
  op?: string;
  affected_stages?: string[];
  rerun?: string;
  [key: string]: unknown;
}

export interface AgentArtCharacter {
  character: string;
  portrait: boolean;
  front: boolean;
  cutout: boolean;
  point: boolean;
  think: boolean;
}

export interface AgentArt {
  ok: boolean;
  count: number;
  characters: AgentArtCharacter[];
  policy?: { characters?: Record<string, { enabled?: string[]; disabled?: string[] }> };
}

async function req<T>(path: string, init?: RequestInit, timeoutMs = 30000): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(API_BASE + path, {
      headers: init?.body ? { "Content-Type": "application/json" } : undefined,
      ...init,
      signal: controller.signal,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`HTTP ${res.status} ${text.slice(0, 240)}`);
    }
    return (await res.json()) as T;
  } finally {
    window.clearTimeout(timer);
  }
}

export const agentapi = {
  overview: () => req<AgentOverview>("/v6/agent/overview"),
  workflow: (template: string) =>
    req<{ ok: boolean; template: string; workflow: AgentWorkflow }>(
      `/v6/agent/workflow/${encodeURIComponent(template)}`
    ),
  saveWorkflow: (template: string, workflow: AgentWorkflow) =>
    req<{ ok: boolean; path: string }>(`/v6/agent/workflow/${encodeURIComponent(template)}`, {
      method: "PUT",
      body: JSON.stringify({ workflow }),
    }),
  run: (payload: {
    template: string;
    duration?: number;
    stages?: string[] | null;
    goal?: string;
    dry_run?: boolean;
    force?: boolean;
  }) =>
    req<{ ok: boolean; job_id: string }>("/v6/agent/run", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  runs: () =>
    req<{ ok: boolean; jobs: AgentJob[]; disk_runs: Record<string, unknown>[] }>(
      "/v6/agent/runs"
    ),
  job: (jobId: string) => req<AgentJob>(`/v6/agent/runs/${encodeURIComponent(jobId)}`),
  edit: (payload: Record<string, unknown>) =>
    req<AgentEditResult>("/v6/agent/edit", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  art: () => req<AgentArt>("/v6/agent/art"),
  pose: (character: string, pose: string, enabled: boolean) =>
    req<AgentEditResult>("/v6/agent/pose", {
      method: "POST",
      body: JSON.stringify({ character, pose, enabled }),
    }),
  runFileUrl: (runId: string, file: string) =>
    mediaUrl(`/media/agent_runs/${encodeURIComponent(runId)}/${file}`),
};

export function errorText(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}
