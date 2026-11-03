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
    async (nextPlatform: PublishPlatform, force = false) => {
      if (force) setRebuilding(true);
      else setLoading(true);
      setErrors([]);
      try {
        const res = await cinemaApi.publishPackage(templateId, { platform: nextPlatform, force });
        if (!aliveRef.current) return;
        setPkg(res);
        notify(
          res.cached
            ? `已复用发布包缓存（${PLATFORMS.find((item) => item.id === nextPlatform)?.label || nextPlatform}）`
            : "发布包已生成",
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
    void build("generic", false);
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
    };
  }, [pkg, platform, templateId, templateLabel]);

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

  const tips = PLATFORM_TIPS[platform];
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
                  setPlatform(item.id);
                  void build(item.id, false);
                }}
              >
                {item.label}
              </button>
            ))}
          </div>

          {errors.length > 0 && (
            <div className="v21-error">
              <b>发布包导出失败（{errors.length} 项）</b>
              <ul>
                {errors.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
              <div style={{ marginTop: 6 }}>
                <button type="button" className="cm-btn cm-btn-sm" onClick={() => void build(platform, true)}>
                  <IconRefresh size={12} /> 重试
                </button>
              </div>
            </div>
          )}

          {loading || rebuilding ? (
            <div className="v26-loading">
              <Spinner size={18} />
              <div>
                <b>{rebuilding ? "正在重新打包…" : "正在打包发布包…"}</b>
                <span>视频/封面/字幕/元数据写入 zip，通常 2–10 秒</span>
              </div>
            </div>
          ) : pkg ? (
            <>
              <div className="v26-summary">
                <div className="v26-summary-item"><span>平台</span><b>{PLATFORMS.find((item) => item.id === platform)?.label || platform}</b></div>
                <div className="v26-summary-item"><span>包大小</span><b>{fmtBytes(pkg.size_bytes)}</b></div>
                <div className="v26-summary-item"><span>文件</span><b>{pkg.files.length} 个</b></div>
                <div className="v26-summary-item"><span>状态</span><b>{pkg.cached ? "缓存复用" : "新生成"}</b></div>
              </div>
              <div className="v26-files">
                <div className="v26-files-head">
                  <span>文件清单</span>
                  <span className="cm-mono">{pkg.package_url.split("/").pop()}</span>
                </div>
                {pkg.files.map((file) => (
                  <div key={file.name} className="v26-file">
                    {file.name.startsWith("video/") ? <IconVideo size={13} /> : file.name.startsWith("cover/") ? <IconImage size={13} /> : <IconCheck size={12} />}
                    <span className="v26-file-name">{file.name}</span>
                    <span className="v26-file-size cm-mono">{fmtBytes(file.size)}</span>
                  </div>
                ))}
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
                <button type="button" className="cm-btn cm-btn-sm cm-btn-ghost" onClick={() => void build(platform, true)} disabled={rebuilding}>
                  <IconRefresh size={12} /> 重新打包
                </button>
              </div>
            </>
          ) : (
            <div className="v18-empty">
              <IconDownload size={26} />
              <b>还没有发布包</b>
              <span>点击下方按钮生成（需该模板已有成片 final.mp4）</span>
              <button type="button" className="cm-btn cm-btn-primary cm-btn-sm" onClick={() => void build(platform, false)}>
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
