# -*- coding: utf-8 -*-
"""R8：在 lib/agent.ts 增加模板级云端 Muse（POST /v6/muse/video/template + 任务轮询）。"""
p = 'frontend/src/lib/agent.ts'
s = open(p, encoding='utf-8').read()

# 1) API 方法：插在 museVideoJob 之后
old = '''  museVideoJob: async (jobId: string, signal?: AbortSignal): Promise<MuseVideoJob> =>
    normalizeMuseVideoJob(await request("/v6/muse/video/jobs/" + enc(jobId), signal ? { signal } : undefined, 20000)),
};'''
new = '''  museVideoJob: async (jobId: string, signal?: AbortSignal): Promise<MuseVideoJob> =>
    normalizeMuseVideoJob(await request("/v6/muse/video/jobs/" + enc(jobId), signal ? { signal } : undefined, 20000)),

  /* ---------------------- R8：模板级云端 Muse（每镜按角色立绘） ---------------------- */
  /** 起一个整集任务；后端已有任务时抛 ApiError(409) */
  museTemplate: async (body: MuseTemplateRequest): Promise<{ job_id: string }> => {
    const raw = await request<unknown>("/v6/muse/video/template", { method: "POST", body: JSON.stringify(body) }, 30000);
    return { job_id: str(rec(raw).job_id) };
  },

  museTemplateJob: async (jobId: string, signal?: AbortSignal): Promise<MuseTemplateJob> =>
    normalizeMuseTemplateJob(
      await request("/v6/muse/video/jobs/" + enc(jobId), signal ? { signal } : undefined, 20000),
    ),
};'''
assert s.count(old) == 1
s = s.replace(old, new, 1)

# 2) 类型 + 归一化 + final 路径转 /media：追加到文件末尾
tail = '''

/* ------------------------- R8：模板级云端 Muse（每镜按角色） ------------------------- */

export interface MuseTemplateRequest {
  template: string;
  /** 段数（每段 10s） */
  segments?: number;
  limit?: number;
  /** 首尾帧衔接：用上一段最后一帧当下一段首帧 */
  chain?: boolean;
  force?: boolean;
  assemble?: boolean;
  dry_run?: boolean;
}

export interface MuseTemplateJob {
  job_id: string;
  /** running | done | failed */
  status: string;
  /** 当前阶段文案（后端 prog 回调写入） */
  stage: string;
  /** 逐行日志（"[角色] 消息"） */
  logs: string[];
  ok_count: number;
  failed: string[];
  /** 成片路径（outputs/... 或 /media/...） */
  final: string;
  error: string;
  template: string;
}

export function normalizeMuseTemplateJob(raw: unknown): MuseTemplateJob {
  const item = rec(raw);
  return {
    job_id: str(item.job_id || item.id),
    status: str(item.status, "queued").toLowerCase(),
    stage: str(item.stage),
    logs: arr(item.logs).map((line) => (typeof line === "string" ? line : str(rec(line).msg))),
    ok_count: num(item.ok_count, 0),
    failed: arr(item.failed).map((line) => str(line)),
    final: str(item.final),
    error: str(item.error),
    template: str(item.template),
  };
}

/** 任务是否还在跑 */
export function isMuseTemplateActive(job: MuseTemplateJob | null): boolean {
  if (!job) return false;
  return job.status === "running" || job.status === "queued";
}

/** 从 409 的 detail 里抠出已在跑的 job_id（后端格式：已有 Muse 视频任务在跑: muset_xxx） */
export function runningJobIdFrom409(message: string): string {
  const hit = /muset_[a-z0-9]+/i.exec(String(message || ""));
  return hit ? hit[0] : "";
}

/** 成片路径 → 可播放 URL（outputs/... → /media/...） */
export function museFinalUrl(raw: string): string {
  const text = String(raw || "").replace(/\\\\/g, "/");
  if (!text) return "";
  if (/^https?:/i.test(text)) return text;
  if (text.startsWith("/media/")) return mediaUrl(text);
  const cut = text.indexOf("/outputs/");
  if (cut >= 0) return mediaUrl("/media/" + text.slice(cut + "/outputs/".length));
  if (text.startsWith("outputs/")) return mediaUrl("/media/" + text.slice("outputs/".length));
  return mediaUrl(text);
}
'''
open(p, 'w', encoding='utf-8', newline='').write(s.rstrip() + tail)
print('agent.ts extended')
