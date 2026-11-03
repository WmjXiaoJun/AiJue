// V29 · 浏览器 blob 下载（发布包 zip 等）
export interface BlobDownloadResult {
  filename: string;
  size: number;
}

export async function downloadUrlAsBlob(url: string, fallbackName = "download.bin"): Promise<BlobDownloadResult> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`下载失败（HTTP ${res.status}）`);
  const blob = await res.blob();
  const filename = (() => {
    try {
      const clean = url.split("?")[0];
      return decodeURIComponent(clean.split("/").pop() || "") || fallbackName;
    } catch {
      return fallbackName;
    }
  })();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 5000);
  return { filename, size: blob.size };
}
