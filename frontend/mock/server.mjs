// 造片局 · 前端联调用的 mock API（仅本地开发使用，不属于交付产物）
// 用法：node mock/server.mjs [port]   （默认 8090，与 vite proxy 对齐）
// 依据 contract/API.md 与 contract/demo_episode.json 返回固定结构，并模拟任务进度，
// 让前端在后端未就绪时也能完整联调：拆镜头 / 批量生成 / 版本 / 选用 / 渲染 / 费用。
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..", "..");
const port = Number(process.argv[2] || 8090);
const demo = JSON.parse(fs.readFileSync(path.join(root, "contract", "demo_episode.json"), "utf8"));

const MEDIA_DIR = path.join(__dirname, "media");
fs.mkdirSync(MEDIA_DIR, { recursive: true });

const now = () => new Date().toISOString();
const rnd = () => crypto.randomBytes(4).toString("hex");

// ---------- 种子数据（与后端播种保持一致） ----------
const project = {
  id: demo.project.id,
  name: demo.project.name,
  format_id: demo.project.format_id,
  aspect: demo.project.aspect,
  status: "active",
  budget: 120,
  spent: 0,
  created_at: new Date(Date.now() - 86400000).toISOString(),
  cover: null,
  episode_count: 1,
};

const episode = {
  id: demo.episode.id,
  project_id: project.id,
  no: demo.episode.no,
  title: demo.episode.title,
  target_duration: demo.episode.target_duration,
  status: "draft",
  progress: 0,
  shots_total: demo.shots.length,
  shots_done: 0,
  cost: 0,
  render_url: null,
  created_at: project.created_at,
};

let assets = demo.assets.map((asset) => ({
  ...asset,
  episode_id: episode.id,
  images: asset.images || [],
  locked: asset.locked ?? false,
}));

let shots = demo.shots.map((shot, index) => ({
  id: shot.id,
  episode_id: episode.id,
  segment_id: shot.segment,
  no: index + 1,
  type: shot.type,
  duration: shot.duration,
  speaker: shot.speaker,
  speaker_name: shot.speaker_name || "",
  line: shot.line || "",
  visual: shot.visual || "",
  lower_third: shot.lower_third || null,
  punchline: shot.punchline || "",
  sfx: shot.sfx || "",
  mood: shot.mood || "",
  status: "pending",
  selected_version_id: null,
  title: shot.title,
  subtitle: shot.subtitle,
}));

let jobs = [];
let versions = [];
let storyboardGenerated = 0;
const settings = {
  generation_mode: "mock",
  comfyui_url: "http://127.0.0.1:8188",
  workflow_template: "{\"1\": {\"class_type\": \"MiniMaxH3\"}}",
  resolution: "1280x720",
  fps: 25,
  tts_provider: "edge-tts",
  voice_map: {
    host: "zh-CN-YunxiNeural",
    guest_a: "zh-CN-XiaoxiaoNeural",
    guest_b: "zh-CN-YunjianNeural",
  },
  h3_price_per_second: 0.09,
};

// ---------- 生成两份演示视频，避免 <video> 404 ----------
function ensureMedia() {
  const ffmpeg = path.join(root, "tools", "node_modules", "ffmpeg-static", "ffmpeg.exe");
  if (!fs.existsSync(ffmpeg)) return;
  if (!fs.existsSync(path.join(MEDIA_DIR, "clip.mp4"))) {
    spawnSync(ffmpeg, ["-y", "-f", "lavfi", "-i", "color=c=0x1a2130:s=640x360:d=3:r=25", "-f", "lavfi", "-i", "sine=frequency=330:duration=3", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path.join(MEDIA_DIR, "clip.mp4")], { stdio: "ignore" });
  }
  if (!fs.existsSync(path.join(MEDIA_DIR, "final.mp4"))) {
    spawnSync(ffmpeg, ["-y", "-f", "lavfi", "-i", "color=c=0x1a1206:s=640x360:d=8:r=25", "-f", "lavfi", "-i", "sine=frequency=440:duration=8", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path.join(MEDIA_DIR, "final.mp4")], { stdio: "ignore" });
  }
}
ensureMedia();

// ---------- 任务模拟 ----------
function makeJob(type, patch) {
  const job = {
    id: "job_" + rnd(),
    type,
    status: "queued",
    progress: 0,
    message: type === "render" ? "排队中：准备渲染工作目录" : "排队中：等待生成槽位",
    episode_id: episode.id,
    shot_id: null,
    output_url: null,
    cost: 0,
    error: null,
    created_at: now(),
    updated_at: now(),
    ...patch,
  };
  jobs.unshift(job);
  return job;
}

function patchJob(job, data) {
  Object.assign(job, data, { updated_at: now() });
}

function recalcEpisode() {
  episode.shots_done = shots.filter((shot) => shot.status === "ready").length;
  episode.shots_total = shots.length;
  episode.progress = shots.length ? Math.round((episode.shots_done / shots.length) * 100) : 0;
  if (episode.shots_done === shots.length && episode.status !== "rendering") episode.status = "storyboard";
  episode.cost = Math.round(jobs.filter((job) => job.status === "done").reduce((sum, job) => sum + (job.cost || 0), 0) * 100) / 100;
  project.spent = episode.cost;
}

function runShotJob(job, shot) {
  shot.status = "queued";
  let progress = 0;
  const timer = setInterval(() => {
    progress += 20;
    if (progress < 100) {
      patchJob(job, { status: "running", progress, message: "渲染中：" + shot.id + " · " + (shot.line || shot.visual || "画面") });
      shot.status = "generating";
      return;
    }
    clearInterval(timer);
    const version = {
      id: "ver_" + rnd(),
      shot_id: shot.id,
      version: versions.filter((item) => item.shot_id === shot.id).length + 1,
      status: "ready",
      video_url: "/media/episodes/" + episode.id + "/shots/" + shot.id + ".mp4",
      thumb_url: null,
      cost: Math.round((shot.duration || 5) * settings.h3_price_per_second * 100) / 100,
      params: { mode: "mock" },
      created_at: now(),
    };
    versions.push(version);
    shot.selected_version_id = version.id;
    shot.status = "ready";
    patchJob(job, {
      status: "done",
      progress: 100,
      message: "完成：已生成 V" + version.version,
      output_url: version.video_url,
      cost: version.cost,
    });
    recalcEpisode();
  }, 220);
}

function runRenderJob(job) {
  episode.status = "rendering";
  let progress = 0;
  const timer = setInterval(() => {
    progress += 8;
    if (progress < 100) {
      patchJob(job, { status: "running", progress, message: "合成中：拼接镜头 " + Math.min(shots.length, Math.ceil((progress / 100) * shots.length)) + "/" + shots.length });
      return;
    }
    clearInterval(timer);
    const total = Math.round(episode.shots_done * 6 * settings.h3_price_per_second * 100) / 100 || 0;
    episode.status = "rendered";
    episode.render_url = "/media/episodes/" + episode.id + "/final.mp4";
    patchJob(job, {
      status: "done",
      progress: 100,
      message: "成片已输出 final.mp4",
      output_url: episode.render_url,
      cost: Math.round(total * 100) / 100,
    });
    recalcEpisode();
  }, 320);
}

// ---------- HTTP ----------
const json = (res, status, data) => {
  const body = JSON.stringify(data);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
  });
  res.end(body);
};

const readBody = (req) =>
  new Promise((resolve) => {
    let raw = "";
    req.on("data", (chunk) => (raw += chunk));
    req.on("end", () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        resolve({});
      }
    });
  });

function serveMedia(req, res, mediaPath) {
  const file = mediaPath.includes("final.mp4") ? path.join(MEDIA_DIR, "final.mp4") : path.join(MEDIA_DIR, "clip.mp4");
  if (!fs.existsSync(file)) {
    res.writeHead(404);
    res.end("no media");
    return;
  }
  const stat = fs.statSync(file);
  const range = req.headers.range;
  const headers = { "Content-Type": "video/mp4", "Accept-Ranges": "bytes", "Access-Control-Allow-Origin": "*" };
  if (range) {
    const match = /bytes=(\d+)-(\d*)/.exec(range);
    const start = match ? Number(match[1]) : 0;
    const end = match && match[2] ? Number(match[2]) : stat.size - 1;
    res.writeHead(206, { ...headers, "Content-Range": "bytes " + start + "-" + end + "/" + stat.size, "Content-Length": end - start + 1 });
    fs.createReadStream(file, { start, end }).pipe(res);
  } else {
    res.writeHead(200, { ...headers, "Content-Length": stat.size });
    fs.createReadStream(file).pipe(res);
  }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://127.0.0.1:" + port);
  const p = url.pathname;
  const method = req.method || "GET";

  if (method === "OPTIONS") return json(res, 204, {});

  if (p.startsWith("/media/")) return serveMedia(req, res, p);

  if (p === "/api/health") return json(res, 200, { status: "ok", version: "0.1.0-mock", mode: "mock" });

  if (p === "/api/projects" && method === "GET") return json(res, 200, [project]);

  if (p === "/api/projects" && method === "POST") {
    const body = await readBody(req);
    const id = "proj_" + rnd();
    return json(res, 200, {
      id,
      name: body.name || "未命名项目",
      format_id: body.format_id || "roundtable_5min",
      aspect: body.aspect || "16:9",
      status: "draft",
      budget: 120,
      spent: 0,
      created_at: now(),
      cover: null,
      episode_count: 0,
    });
  }

  if (p === "/api/projects/" + project.id && method === "GET") return json(res, 200, project);
  if (p === "/api/projects/" + project.id + "/episodes") return json(res, 200, [episode]);

  if (p === "/api/episodes/" + episode.id && method === "GET") return json(res, 200, episode);
  if (p === "/api/episodes/" + episode.id && method === "PATCH") {
    const body = await readBody(req);
    if (body.title) episode.title = body.title;
    if (body.target_duration) episode.target_duration = body.target_duration;
    return json(res, 200, episode);
  }
  if (p === "/api/episodes/" + episode.id + "/costs") {
    const byType = {};
    jobs.filter((job) => job.status === "done").forEach((job) => {
      byType[job.type] = Math.round(((byType[job.type] || 0) + (job.cost || 0)) * 100) / 100;
    });
    const items = [
      { label: "镜头生成 ×" + shots.filter((shot) => shot.status === "ready").length, amount: byType.shot || 0 },
      { label: "成片渲染 ×" + jobs.filter((job) => job.type === "render" && job.status === "done").length, amount: byType.render || 0 },
      { label: "TTS 配音（mock）", amount: 0 },
    ];
    return json(res, 200, { currency: "CNY", total: episode.cost, by_type: byType, items });
  }

  if (p === "/api/episodes/" + episode.id + "/assets" && method === "GET") return json(res, 200, assets);
  if (p === "/api/episodes/" + episode.id + "/assets" && method === "POST") {
    const body = await readBody(req);
    const asset = { id: "asset_" + rnd(), episode_id: episode.id, images: [], locked: false, ...body };
    assets = [...assets, asset];
    return json(res, 200, asset);
  }
  if (p.startsWith("/api/assets/") && method === "PATCH") {
    const id = p.split("/").pop();
    const target = assets.find((asset) => asset.id === id);
    if (!target) return json(res, 404, { detail: "asset not found" });
    const body = await readBody(req);
    Object.assign(target, body);
    return json(res, 200, target);
  }

  if (p === "/api/episodes/" + episode.id + "/shots" && method === "GET") return json(res, 200, shots);
  if (p.startsWith("/api/shots/") && p.endsWith("/versions") && method === "GET") {
    const id = p.split("/")[3];
    return json(res, 200, versions.filter((version) => version.shot_id === id));
  }
  if (p.startsWith("/api/shots/") && p.includes("/versions/") && p.endsWith("/select") && method === "POST") {
    const parts = p.split("/");
    const shot = shots.find((item) => item.id === parts[3]);
    const version = versions.find((item) => item.id === parts[5]);
    if (!shot || !version) return json(res, 404, { detail: "not found" });
    shot.selected_version_id = version.id;
    return json(res, 200, shot);
  }
  if (p.startsWith("/api/shots/") && p.endsWith("/generate") && method === "POST") {
    const id = p.split("/")[3];
    const shot = shots.find((item) => item.id === id);
    if (!shot) return json(res, 404, { detail: "shot not found" });
    if (shot.status === "generating" || shot.status === "queued") return json(res, 409, { detail: "该镜头正在生成中" });
    const job = makeJob("shot", { shot_id: shot.id, message: "排队中：" + shot.id });
    runShotJob(job, shot);
    return json(res, 200, job);
  }
  if (p.startsWith("/api/shots/") && method === "PATCH") {
    const id = p.split("/")[3];
    const shot = shots.find((item) => item.id === id);
    if (!shot) return json(res, 404, { detail: "shot not found" });
    const body = await readBody(req);
    Object.assign(shot, body);
    return json(res, 200, shot);
  }

  if (p === "/api/episodes/" + episode.id + "/storyboard/generate" && method === "POST") {
    storyboardGenerated = shots.length;
    return json(res, 200, { created: storyboardGenerated });
  }
  if (p === "/api/episodes/" + episode.id + "/render" && method === "POST") {
    const running = jobs.find((job) => job.type === "render" && (job.status === "queued" || job.status === "running"));
    if (running) return json(res, 409, { detail: "已有渲染任务进行中" });
    const job = makeJob("render", { message: "排队中：准备渲染工作目录" });
    runRenderJob(job);
    return json(res, 200, job);
  }

  if (p === "/api/jobs" && method === "GET") {
    let list = jobs.slice();
    const type = url.searchParams.get("type");
    const status = url.searchParams.get("status");
    const episodeId = url.searchParams.get("episode_id");
    if (type) list = list.filter((job) => job.type === type);
    if (status) list = list.filter((job) => job.status === status);
    if (episodeId) list = list.filter((job) => job.episode_id === episodeId);
    return json(res, 200, list);
  }
  if (p.startsWith("/api/jobs/") && p.endsWith("/retry") && method === "POST") {
    const id = p.split("/")[3];
    const job = jobs.find((item) => item.id === id);
    if (!job) return json(res, 404, { detail: "job not found" });
    if (job.type === "shot") {
      const shot = shots.find((item) => item.id === job.shot_id);
      if (shot) {
        patchJob(job, { status: "queued", progress: 0, error: null, message: "重试排队中" });
        runShotJob(job, shot);
      }
    } else {
      patchJob(job, { status: "queued", progress: 0, error: null, message: "重试排队中" });
      runRenderJob(job);
    }
    return json(res, 200, job);
  }
  if (p.startsWith("/api/jobs/") && method === "GET") {
    const id = p.split("/")[3];
    const job = jobs.find((item) => item.id === id);
    if (!job) return json(res, 404, { detail: "job not found" });
    return json(res, 200, job);
  }

  if (p === "/api/settings" && method === "GET") return json(res, 200, settings);
  if (p === "/api/settings" && method === "PATCH") {
    const body = await readBody(req);
    Object.assign(settings, body);
    return json(res, 200, settings);
  }

  json(res, 404, { detail: "mock not found: " + method + " " + p });
});

server.listen(port, "127.0.0.1", () => {
  console.log("[mock] 造片局 mock API → http://127.0.0.1:" + port + "/api  (media: " + MEDIA_DIR + ")");
});
