// V26 · 发布包导出弹层（文件清单 / 下载 zip / 复制元数据 / 平台提醒）
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, errorText } from "../../lib/api";
import { mediaSrc } from "../../lib/v5";
import type { PublishPackageResponse, PublishPlatform } from "../../lib/cinema";
import { cinemaApi } from "../../lib/cinema";
import { useToast } from "../Toast";
import { Spinner } from "../ui";
import { IconAlert, IconCheck, IconClose, IconDownload, IconImage, IconRefresh, IconVideo } from "../icons";
import "../../styles/publish.css";

const PLATFORMS: { id: PublishPlatform; label: string }[] = [
  { id: "generic", label: "本地/通用" },
  { id: "youtube", label: "YouTube" },
  { id: "tiktok", label: "TikTok" },
  { id: "instagram", label: "Instagram" },
];

const PLATFORM_TIPS: Record<PublishPlatform, string[]> = {
  generic: ["视频 1280×720 / 25fps；封面建议 1280×720", "字幕已随包提供（zh.srt），可按平台要求重新烧录"],
  youtube: ["标题 ≤100 字符；简介含 2–3 句看点 + 话题标签", "Tags 已写入 metadata.json，可直接复制", "Shorts 建议单独裁 9:16 版本"],
  tiktok: ["TikTok 需 9:16 竖屏：本包为 16:9 母版，请使用平台内裁切或另行重组", "字幕建议烧录进画面（平台对 SRT 支持有限）"],
  instagram: ["IG Reels 需 9:16 竖屏：同 TikTok，需裁切/重组", "封面用包内 cover.jpg（1280×720），可再裁 1080×1350"],
};

type VerticalMode = "blur_pad" | "crop";

const VERTICAL_MODES: { id: VerticalMode; label: string; desc: string }[] = [
  { id: "blur_pad", label: "模糊背景填充", desc: "完整保留 16:9 画面，上下用模糊背景填充（默认）" },
  { id: "crop", label: "中心裁切 9:16", desc: "裁切画面中心并放大到 1080×1920，适合主体居中素材" },
];

/** V27：TikTok/IG 默认附带竖版 9:16 */
function defaultVertical(platform: PublishPlatform): boolean {
  return platform === "tiktok" || platform === "instagram";
}

function fmtBytes(bytes: number): string {
  if (!bytes) return "—";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1024 / 1024).toFixed(2) + " MB";
}

function friendlyPublishError(err: unknown): string[] {
  if (err instanceof ApiError) {
    if (err.status === 404 || err.status === 405) return ["发布包接口尚未就绪（后端 V26 实现中），请稍后重试"];
    if (err.status === 400) return [err.message || "该模板暂无成片，无法导出发布包"];
    return [`发布包导出失败（${err.status}）：${err.message}`];
  }
  if (err instanceof Error) return ["发布包导出失败：" + err.message];
  return ["发布包导出失败：" + String(err)];
}

export function PublishPackageModal({
  open,
  templateId,
  templateLabel,
  onClose,
}: {
  open: boolean;
  templateId: string;
  templateLabel?: string;
  onClose: () => void;
}) {
  const notify = useToast();
  const [platform, setPlatform] = useState<PublishPlatform>("generic");
  const [vertical, setVertical] = useState(false);
  const [verticalMode, setVerticalMode] = useState<VerticalMode>("blur_pad");
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [pkg, setPkg] = useState<PublishPackageResponse | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [copied, setCopied] = useState(false);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const build = useCallback(
    async (nextPlatform: PublishPlatform, nextVertical: boolean, nextMode: VerticalMode, force = false) => {
      if (force) setRebuilding(true);
      else setLoading(true);
      setErrors([]);
      try {
        const res = await cinemaApi.publishPackage(templateId, {
          platform: nextPlatform,
          force,
          vertical: nextVertical,
          vertical_mode: nextMode,
        });
        if (!aliveRef.current) return;
        setPkg(res);
        notify(
          res.cached
            ? `已复用发布包缓存（${PLATFORMS.find((item) => item.id === nextPlatform)?.label || nextPlatform}${nextVertical ? " · 含竖版" : ""}）`
            : `发布包已生成${nextVertical ? "（含竖版 9:16）" : ""}`,
          "success",
        );
      } catch (err) {
        if (!aliveRef.current) return;
        const items = friendlyPublishError(err);
        setErrors(items);
        notify(items[0], "error");
      } finally {
        if (aliveRef.current) {
          setLoading(false);
          setRebuilding(false);
        }
      }
    },
    [notify, templateId],
  );

  useEffect(() => {
    if (!open) return;
    setPkg(null);
    setErrors([]);
    setCopied(false);
    setPlatform("generic");
    setVertical(false);
    setVerticalMode("blur_pad");
    void build("generic", false, "blur_pad", false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, templateId]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !downloading) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [downloading, onClose, open]);

  const metadata = useMemo(() => {
    if (pkg?.metadata && Object.keys(pkg.metadata).length) return pkg.metadata;
    return {
      title: templateLabel || templateId,
      description: `${templateLabel || templateId} · 造片局 AI 3D 成片`,
      tags: ["AI视频", "3D", templateId],
      template_id: templateId,
      platform,
      resolution: "1280x720",
      fps: 25,
      duration_sec: null,
      generated_at: null,
      package: pkg?.package_url || null,
      ...(vertical ? { vertical: { file: "video/final_vertical.mp4", resolution: "1080x1920", mode: verticalMode } } : {}),
    };
  }, [pkg, platform, templateId, templateLabel, vertical, verticalMode]);

  const download = async () => {
    if (!pkg?.package_url) return;
    setDownloading(true);
    try {
      const url = mediaSrc(pkg.package_url);
      const res = await fetch(url);
      if (!res.ok) throw new Error(`下载失败（HTTP ${res.status}）`);
      const blob = await res.blob();
      const filename = pkg.package_url.split("/").pop() || `${templateId}_publish.zip`;
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 4000);
      notify(`发布包已开始下载：${filename}（${fmtBytes(blob.size)}）`, "success");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setErrors([message]);
      notify("下载失败：" + message, "error");
    } finally {
      setDownloading(false);
    }
  };

  const copyMetadata = async () => {
    const text = JSON.stringify(metadata, null, 2);
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      notify("元数据已复制到剪贴板", "success");
      window.setTimeout(() => setCopied(false), 2500);
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy");
        setCopied(true);
        notify("元数据已复制到剪贴板", "success");
      } catch {
        setErrors(["复制失败：浏览器不允许访问剪贴板，请手动复制"]);
      }
      area.remove();
    }
  };

  if (!open) return null;

  const tips = (() => {
    const base = PLATFORM_TIPS[platform];
    if (vertical) {
      const modeLabel = verticalMode === "crop" ? "中心裁切" : "模糊背景填充";
      const verticalTip = defaultVertical(platform)
        ? `已附带竖版 9:16（${modeLabel}）：可直接上传 TikTok/IG；${verticalMode === "crop" ? "画面按中心裁切放大，上下部分会被裁掉" : "竖版为模糊背景填充，完整保留画面"}`
        : `已附带竖版 9:16（1080×1920，${modeLabel}）：可上传 TikTok/IG`;
      return [verticalTip, ...base.filter((tip) => !tip.includes("9:16"))];
    }
    return base;
  })();
  return (
    <div className="v26-mask" onMouseDown={() => !downloading && onClose()} role="presentation">
      <div className="v26-modal" onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label="发布包导出">
        <header className="v26-head">
          <div style={{ flex: "1 1 auto", minWidth: 0 }}>
            <span className="v21-drawer-kicker">PUBLISH PACKAGE · 发布包</span>
            <h3 className="v21-drawer-title">导出发布包</h3>
            <p className="v21-drawer-sub">
              {templateLabel || templateId} · 成片 + 封面 + 中文字幕 + 元数据 + 上传说明（本地 zip，手动上传）
            </p>
          </div>
          <button type="button" className="v21-close" onClick={onClose} aria-label="关闭" disabled={downloading}>
            <IconClose size={16} />
          </button>
        </header>

        <div className="v26-body">
          <div className="v26-platforms" role="group" aria-label="目标平台">
            {PLATFORMS.map((item) => (
              <button
                key={item.id}
                type="button"
                className={"v26-platform" + (platform === item.id ? " is-active" : "")}
                disabled={loading || rebuilding}
                onClick={() => {
                  const nextVertical = defaultVertical(item.id);
                  setPlatform(item.id);
                  setVertical(nextVertical);
                  setVerticalMode("blur_pad");
                  void build(item.id, nextVertical, "blur_pad", false);
                }}
              >
                {item.label}
              </button>
            ))}
          </div>

          <label className={"v27-vertical" + (vertical ? " is-on" : "")}>
            <input
              type="checkbox"
              checked={vertical}
              disabled={loading || rebuilding}
              onChange={(event) => setVertical(event.target.checked)}
            />
            <span className="v27-vertical-main">
              <b>附带竖版 9:16（TikTok/IG）</b>
              <em>1080×1920 · 模糊背景填充，完整保留 16:9 画面；含竖版封面</em>
            </span>
            {defaultVertical(platform) && <span className="v27-vertical-default">平台默认</span>}
          </label>
          {vertical && (
            <div className="v28-vmode" role="group" aria-label="竖版模式">
              <span className="v28-vmode-label">竖版模式</span>
              {VERTICAL_MODES.map((mode) => (
                <button
                  key={mode.id}
                  type="button"
                  className={"v28-vmode-btn" + (verticalMode === mode.id ? " is-active" : "")}
                  disabled={loading || rebuilding}
                  onClick={() => setVerticalMode(mode.id)}
                  title={mode.desc}
                >
                  {mode.label}
                </button>
              ))}
              <span className="v28-vmode-desc">{VERTICAL_MODES.find((mode) => mode.id === verticalMode)?.desc}</span>
            </div>
          )}
          {vertical && (
            <div className="v27-vertical-preview">
              <span className="v27-vertical-phone" aria-hidden="true" />
              <span>
                将输出 <b className="cm-mono">video/final_vertical.mp4</b>（1080×1920）与 <b className="cm-mono">cover/cover_vertical.jpg</b>；
                若需按当前勾选重新生成，点「重新打包」。
              </span>
            </div>
          )}

          {errors.length > 0 && (
            <div className="v21-error">
              <b>发布包导出失败（{errors.length} 项）</b>
              <ul>
                {errors.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
              <div style={{ marginTop: 6 }}>
                <button type="button" className="cm-btn cm-btn-sm" onClick={() => void build(platform, vertical, verticalMode, true)}>
                  <IconRefresh size={12} /> 重试
                </button>
              </div>
            </div>
          )}

          {loading || rebuilding ? (
            <div className="v26-loading">
              <Spinner size={18} />
              <div>
                <b>{rebuilding ? "正在重新打包…" : "正在打包发布包…"}{vertical ? "（含竖版 9:16）" : ""}</b>
                <span>视频/封面/字幕/元数据写入 zip；竖版需 ffmpeg 生成，通常 3–20 秒</span>
              </div>
            </div>
          ) : pkg ? (
            <>
              <div className="v26-summary">
                <div className="v26-summary-item"><span>平台</span><b>{PLATFORMS.find((item) => item.id === platform)?.label || platform}</b></div>
                <div className="v26-summary-item"><span>包大小</span><b>{fmtBytes(pkg.size_bytes)}</b></div>
                <div className="v26-summary-item"><span>文件</span><b>{pkg.files.length} 个</b></div>
                <div className="v26-summary-item"><span>竖版</span><b>{pkg.files.some((file) => file.name.includes("_vertical")) ? (verticalMode === "crop" ? "已附带·裁切" : "已附带·模糊填充") : "未附带"}</b></div>
                <div className="v26-summary-item"><span>状态</span><b>{pkg.cached ? "缓存复用" : "新生成"}</b></div>
              </div>
              <div className="v26-files">
                <div className="v26-files-head">
                  <span>文件清单</span>
                  <span className="cm-mono">{pkg.package_url.split("/").pop()}</span>
                </div>
                {pkg.files.map((file) => {
                  const isVertical = file.name.includes("_vertical");
                  return (
                    <div key={file.name} className={"v26-file" + (isVertical ? " is-vertical" : "")}>
                      {file.name.startsWith("video/") ? <IconVideo size={13} /> : file.name.startsWith("cover/") ? <IconImage size={13} /> : <IconCheck size={12} />}
                      <span className="v26-file-name">{file.name}</span>
                      {isVertical && (
                        <>
                          <span className="v27-vertical-badge">{verticalMode === "crop" ? "竖版·裁切" : "竖版·模糊"}</span>
                          <span className="v27-vertical-dim">1080×1920 · {verticalMode}</span>
                        </>
                      )}
                      <span className="v26-file-size cm-mono">{fmtBytes(file.size)}</span>
                    </div>
                  );
                })}
                {(pkg.warnings || []).map((warning, index) => (
                  <div key={index} className="v26-warning">
                    <IconAlert size={12} /> {warning}
                  </div>
                ))}
              </div>
              <div className="v26-actions">
                <button type="button" className="cm-btn cm-btn-primary" onClick={() => void download()} disabled={downloading}>
                  {downloading ? <Spinner size={13} /> : <IconDownload size={13} />} {downloading ? "下载中…" : "下载 zip"}
                </button>
                <button type="button" className="cm-btn" onClick={() => void copyMetadata()}>
                  {copied ? <IconCheck size={13} /> : <IconImage size={13} />} {copied ? "已复制元数据" : "复制元数据"}
                </button>
                <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={() => void build(platform, vertical, verticalMode, true)} disabled={rebuilding} title="按当前平台、竖版勾选与模式重新打包（force）">
                  <IconRefresh size={12} /> 重新打包{vertical ? `（含竖版·${verticalMode === "crop" ? "裁切" : "模糊"}）` : ""}
                </button>
              </div>
            </>
          ) : (
            <div className="v18-empty">
              <IconDownload size={26} />
              <b>还没有发布包</b>
              <span>点击下方按钮生成（需该模板已有成片 final.mp4）</span>
              <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={() => void build(platform, vertical, verticalMode, false)}>
                <IconDownload size={12} /> 生成发布包
              </button>
            </div>
          )}

          <div className="v26-tips">
            <b>上传平台提醒</b>
            <ul>
              {tips.map((tip) => (
                <li key={tip}>{tip}</li>
              ))}
              <li>包内 README.txt 含上传步骤；metadata.json 可直接复制到 YouTube/TikTok 描述。</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
