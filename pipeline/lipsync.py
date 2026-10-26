# -*- coding: utf-8 -*-
"""V5 Lip-sync provider（MuseTalk 1.5，vendor 引用 local_models/lipsync/MuseTalk）。

设计要点（见 docs/LIPSYNC_FEASIBILITY.md）：
- 不依赖 mmpose/mmcv/DWPose，也不依赖 face-parsing/BiSeNet：
  * 人脸 bbox 用 OpenCV YuNet（DNN，CPU 20ms/帧，实测动漫脸效果好），
    再用 YuNet 的 5 点 landmark 复刻 MuseTalk 的 “鼻下→下巴” 方形 crop 规则；
  * 贴回用羽化椭圆掩码覆盖嘴部+下巴，避开 face parsing 依赖。
- 模型/代码全部 vendor 引用，不复制大文件进 repo：
  * 代码 local_models/lipsync/MuseTalk（sys.path 指向，按需 import）
  * 权重 local_models/lipsync_models/{musetalkV15,sd-vae,whisper,syncnet}
- 运行环境是独立 venv：local_models/lipsync-venv（torch cu128）。
  主环境（无 torch）调用 sync_clip() 时会自动 subprocess 转发到 venv 执行。

CLI:
  local_models/lipsync-venv/Scripts/python.exe -m pipeline.lipsync \
      --clip outputs/.../h3/v3_s001.mp4 --audio outputs/.../audio/v3_s001.wav \
      --out tmp/v5_lipsync/v3_s001_lipsync.mp4 [--start 1.0 --duration 4.0] [--json]

API:
  available() -> dict
  sync_clip(clip_mp4, audio_wav, out_mp4, *, provider="musetalk", device="cuda",
            workdir=None, start=None, duration=None, face_hint=None) -> dict
  measure_sync(clip_mp4, audio_wav) -> dict | None
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MT_DIR = REPO / "local_models" / "lipsync" / "MuseTalk"
MODELS = REPO / "local_models" / "lipsync_models"
VENV_PY = REPO / "local_models" / "lipsync-venv" / "Scripts" / "python.exe"
_DEFAULT_WORK = REPO / "tmp" / "v5_lipsync"

# MuseTalk 1.5 默认推理参数（对齐官方 scripts/inference.py）
FPS_FALLBACK = 25.0
PADDING_LEFT = 2
PADDING_RIGHT = 2
EXTRA_MARGIN = 10          # v15: face bbox 底部额外扩 10px
BATCH_SIZE = 8
CHEEK = 90                 # v15 blending 参数（这里不用 parsing，仅保留说明）

# 贴回羽化椭圆掩码参数（相对 crop 宽高；cy 中心高度、rx/ry 半径比例、feather 羽化）
_MASK_DEFAULTS = {"cy": 0.72, "rx": 0.46, "ry": 0.30, "feather": 0.10}


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _ffmpeg() -> str:
    p = REPO / "tools" / "node_modules" / "ffmpeg-static" / "ffmpeg.exe"
    return str(p) if p.exists() else "ffmpeg"


def _ffprobe() -> str:
    p = REPO / "tools" / "node_modules" / "ffprobe-static" / "bin" / "win32" / "x64" / "ffprobe.exe"
    return str(p) if p.exists() else "ffprobe"


def _run(cmd: list, *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    kw = dict(encoding="utf-8", errors="replace")
    if capture:
        kw.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return subprocess.run([str(c) for c in cmd], check=check, **kw)


def probe(clip: str | Path) -> dict:
    """ffprobe 读取视频基本信息：width/height/fps/duration/nframes。"""
    cmd = [_ffprobe(), "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames,duration",
           "-show_entries", "format=duration", "-of", "json", str(clip)]
    out = _run(cmd, capture=True).stdout
    data = json.loads(out)
    st = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    num, _, den = str(st.get("r_frame_rate") or "25/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except Exception:
        fps = FPS_FALLBACK
    duration = st.get("duration") or fmt.get("duration") or 0
    w = int(st.get("width") or 0)
    h = int(st.get("height") or 0)
    n = st.get("nb_frames")
    n = int(n) if n not in (None, "N/A", "") else int(round(float(duration) * fps))
    return {"path": str(clip), "width": w, "height": h, "fps": round(fps, 4),
            "duration": round(float(duration), 4), "n_frames": n}


def _model_paths() -> dict:
    return {
        "unet": MODELS / "musetalkV15" / "unet.pth",
        "unet_config": MODELS / "musetalkV15" / "musetalk.json",
        "vae": MODELS / "sd-vae",
        "whisper": MODELS / "whisper",
        "syncnet": MODELS / "syncnet" / "latentsync_syncnet.pt",
        "yunet": MODELS / "yunet" / "face_detection_yunet_2023mar.onnx",
    }


_UNET_BYTES = 3_400_074_924


def available() -> dict:
    """检查 provider 是否可用（可在主环境调用，不 import torch）。"""
    mp = _model_paths()
    files = {k: (str(v), v.exists() if v.suffix else v.is_dir()) for k, v in mp.items()}
    unet_ok = mp["unet"].exists() and mp["unet"].stat().st_size == _UNET_BYTES
    deps = {
        "venv_python": VENV_PY.exists(),
        "musetalk_code": (MT_DIR / "musetalk" / "models" / "unet.py").exists(),
        "ffmpeg": (REPO / "tools" / "node_modules" / "ffmpeg-static" / "ffmpeg.exe").exists(),
        "unet": unet_ok,
        "unet_config": mp["unet_config"].exists(),
        "sd_vae": (mp["vae"] / "diffusion_pytorch_model.bin").exists(),
        "whisper": (mp["whisper"] / "pytorch_model.bin").exists(),
        "yunet": mp["yunet"].exists(),
        "syncnet": mp["syncnet"].exists(),
    }
    ok = all(v for k, v in deps.items() if k not in ("syncnet",))
    return {
        "ok": ok,
        "provider_default": "musetalk",
        "providers": {"musetalk": ok, "wav2lip": False},
        # ---- V5_INTERFACES §5 契约字段（扁平布尔）----
        "musetalk": ok,
        "wav2lip": False,
        "syncnet": bool(deps["syncnet"]),
        "deps": deps,
        "files": files,
        "unet_bytes": mp["unet"].stat().st_size if mp["unet"].exists() else 0,
        "unet_expected_bytes": _UNET_BYTES,
        "torch_in_env": _has_torch(),
        "venv_python": str(VENV_PY),
        "reason": "" if ok else "缺失: " + ", ".join(k for k, v in deps.items() if not v and k != "syncnet"),
    }


def _venv_ready() -> bool:
    """当前解释器是否已具备 MuseTalk 依赖（在 lipsync venv 内为 True）。"""
    return not _missing_deps()


_REQUIRED_MODULES = ("torch", "diffusers", "transformers", "cv2", "einops", "omegaconf")


def _missing_deps() -> list[str]:
    missing = []
    for mod in _REQUIRED_MODULES:
        try:
            __import__(mod)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{mod}({type(exc).__name__}: {exc})")
    return missing


def _in_lipsync_venv() -> bool:
    """当前进程是否就是 local_models/lipsync-venv 的解释器（最可靠的 venv 判定）。"""
    try:
        if not VENV_PY.exists():
            return False
        return Path(sys.executable).resolve() == VENV_PY.resolve()
    except Exception:  # noqa: BLE001
        return False


def _has_torch() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 人脸检测 / crop（YuNet，CPU）
# --------------------------------------------------------------------------- #
_YUNET = None


def _yunet():
    global _YUNET
    if _YUNET is None:
        import cv2
        proto = _model_paths()["yunet"]
        if not proto.exists():
            raise FileNotFoundError(f"YuNet 模型缺失: {proto}")
        _YUNET = cv2.FaceDetectorYN.create(str(proto), "", (320, 320), 0.5, 0.3, 5000)
    return _YUNET


def detect_faces(frame_bgr) -> list[dict]:
    """返回 [{bbox:(x,y,x2,y2), score, landmarks: [(x,y)*5]}]（按面积降序）。"""
    h, w = frame_bgr.shape[:2]
    det = _yunet()
    det.setInputSize((w, h))
    _, faces = det.detect(frame_bgr)
    out = []
    if faces is None:
        return out
    for f in faces:
        # YuNet 输出: [x, y, w, h, 右眼x,y, 左眼x,y, 鼻尖x,y, 右嘴角x,y, 左嘴角x,y, score]
        x1, y1, bw, bh = (float(v) for v in f[:4])
        score = float(f[-1])
        lms = [(float(f[4 + 2 * i]), float(f[5 + 2 * i])) for i in range(5)]
        out.append({"bbox": (x1, y1, x1 + bw, y1 + bh), "score": score, "landmarks": lms})
    out.sort(key=lambda d: (d["bbox"][2] - d["bbox"][0]) * (d["bbox"][3] - d["bbox"][1]), reverse=True)
    return out


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / max(ua, 1e-6)


def bbox_to_crop(bbox, landmarks=None, *, extra_margin: int = EXTRA_MARGIN,
                 frame_hw: tuple[int, int] | None = None) -> tuple[int, int, int, int]:
    """复刻 MuseTalk get_landmark_and_bbox 的核心规则（用 YuNet bbox/landmark 近似）。

    MuseTalk: chin = max(face_landmark_y); nose = face_landmark[29]; upper = nose-(chin-nose)。
    这里：chin = bbox 底（YuNet 的 bbox 通常到下巴）；nose_y 用 landmark[2]（鼻尖），
    退化时用 chin - 0.45*face_h；crop 左右用 bbox 左右（YuNet 已贴脸缘）。
    """
    x1, y1, x2, y2 = (float(v) for v in bbox)
    fh = max(1.0, y2 - y1)
    nose_y = None
    if landmarks and len(landmarks) >= 3:
        nose_y = float(landmarks[2][1])
    if nose_y is None or not (y1 - 0.2 * fh <= nose_y <= y2 + 0.1 * fh):
        nose_y = y2 - 0.45 * fh
    upper = nose_y - (y2 - nose_y)          # 以鼻子为中心上下对称（MuseTalk 规则）
    upper = max(0.0, min(upper, nose_y - 0.35 * fh))
    bottom = y2 + extra_margin
    if frame_hw is not None:
        W, H = frame_hw[1], frame_hw[0]
        x1 = max(0.0, min(x1, W - 2))
        x2 = max(x1 + 2, min(x2, W))
        upper = max(0.0, min(upper, H - 2))
        bottom = max(upper + 2, min(bottom, H))
    return int(round(x1)), int(round(upper)), int(round(x2)), int(round(bottom))


def pick_face(faces: list[dict], prev_bbox=None, hint=None) -> dict | None:
    """选说话人脸：优先 hint 附近；其次与上一帧 IoU/中心最近；否则最大脸。"""
    if not faces:
        return None
    if hint is not None:
        hx, hy = hint
        return min(faces, key=lambda d: ((d["bbox"][0] + d["bbox"][2]) / 2 - hx) ** 2 +
                                          ((d["bbox"][1] + d["bbox"][3]) / 2 - hy) ** 2)
    if prev_bbox is not None:
        pcx, pcy = (prev_bbox[0] + prev_bbox[2]) / 2, (prev_bbox[1] + prev_bbox[3]) / 2
        pw = max(1.0, prev_bbox[2] - prev_bbox[0])
        diag = max(1.0, ((prev_bbox[2] - prev_bbox[0]) ** 2 + (prev_bbox[3] - prev_bbox[1]) ** 2) ** 0.5)
        prev_area = max(1.0, (prev_bbox[2] - prev_bbox[0]) * (prev_bbox[3] - prev_bbox[1]))

        def score(d):
            iou = _iou(d["bbox"], prev_bbox)
            cx, cy = (d["bbox"][0] + d["bbox"][2]) / 2, (d["bbox"][1] + d["bbox"][3]) / 2
            dist = ((cx - pcx) ** 2 + (cy - pcy) ** 2) ** 0.5
            area = max(1.0, (d["bbox"][2] - d["bbox"][0]) * (d["bbox"][3] - d["bbox"][1]))
            # 与上一帧越像越好：IoU 主导，中心距离次之，面积比作平局裁决
            return 2.0 * iou - dist / diag + 0.15 * min(1.0, area / prev_area)

        cand = max(faces, key=score)
        return cand
    return faces[0]


def _smooth_bboxes(boxes: list, alpha: float = 0.65, max_jump: float = 0.35) -> list:
    """EMA 平滑 + 跳变抑制，避免逐帧 crop 抖动。boxes 允许 None（沿用上一帧）。"""
    out = []
    prev = None
    for b in boxes:
        if b is None:
            out.append(prev)
            continue
        b = tuple(float(v) for v in b)
        if prev is None:
            cur = b
        else:
            w = max(1.0, prev[2] - prev[0])
            # 中心跳变 > max_jump*脸宽 时强平滑（视为抖动）
            pcx = (prev[0] + prev[2]) / 2
            ccx = (b[0] + b[2]) / 2
            a = alpha if abs(ccx - pcx) < max_jump * w else 0.2
            cur = tuple(a * nb + (1 - a) * pb for nb, pb in zip(b, prev))
        out.append(cur)
        prev = cur
    # 反向补 None
    for i in range(len(out) - 2, -1, -1):
        if out[i] is None:
            out[i] = out[i + 1]
    for i in range(len(out)):
        if out[i] is None:
            out[i] = boxes[i] if boxes[i] else None
    return out


# --------------------------------------------------------------------------- #
# 贴回掩码（羽化椭圆，替代 face-parsing）
# --------------------------------------------------------------------------- #
def ellipse_mask(crop_w: int, crop_h: int, *, cy: float = 0.72, rx: float = 0.46,
                 ry: float = 0.30, feather: float = 0.10):
    """返回 float32 [crop_h,crop_w] 的 0..1 羽化椭圆掩码（覆盖嘴部+下巴）。"""
    import cv2
    import numpy as np
    m = np.zeros((crop_h, crop_w), dtype=np.float32)
    cv2.ellipse(m, (int(crop_w * 0.5), int(crop_h * cy)),
                (max(2, int(crop_w * rx)), max(2, int(crop_h * ry))),
                0, 0, 360, 1.0, -1)
    k = max(3, int(round(feather * max(crop_w, crop_h))) | 1)
    m = cv2.GaussianBlur(m, (k, k), 0)
    return m


def paste_face(frame, gen_face, crop_box, *, mask_params=None):
    """把 256x256 生成脸贴回原帧（羽化椭圆混叠）。"""
    import cv2
    import numpy as np
    x1, y1, x2, y2 = crop_box
    w, h = max(1, x2 - x1), max(1, y2 - y1)
    face = cv2.resize(gen_face, (w, h), interpolation=cv2.INTER_LANCZOS4)
    mp = dict(cy=0.72, rx=0.46, ry=0.30, feather=0.10)
    if mask_params:
        mp.update(mask_params)
    m = ellipse_mask(w, h, **mp)[..., None]
    roi = frame[y1:y2, x1:x2].astype(np.float32)
    blended = face.astype(np.float32) * m + roi * (1.0 - m)
    out = frame.copy()
    out[y1:y2, x1:x2] = np.clip(blended, 0, 255).astype(np.uint8)
    return out


# --------------------------------------------------------------------------- #
# MuseTalk 推理（在 venv 内）
# --------------------------------------------------------------------------- #
def _setup_vendor_path():
    if str(MT_DIR) not in sys.path:
        sys.path.insert(0, str(MT_DIR))


def _install_torchvision_shim():
    """MuseTalk 的 vae.py 只用到 torchvision.transforms.Normalize。

    为了不引入体积大且需与 torch 精确匹配的 torchvision，这里注入一个等价的最小 shim。
    """
    try:
        import torchvision  # noqa: F401
        return
    except Exception:  # noqa: BLE001
        pass
    import types
    import torch

    tv = types.ModuleType("torchvision")
    tr = types.ModuleType("torchvision.transforms")

    class Normalize(torch.nn.Module):
        def __init__(self, mean, std, inplace: bool = False):
            super().__init__()
            m = torch.as_tensor(mean, dtype=torch.float32).view(-1, 1, 1)
            s = torch.as_tensor(std, dtype=torch.float32).view(-1, 1, 1)
            self.register_buffer("mean", m)
            self.register_buffer("std", s)
            self.inplace = inplace

        def forward(self, tensor):
            dtype = tensor.dtype
            out = (tensor - self.mean.to(tensor.device, dtype)) / self.std.to(tensor.device, dtype)
            return out

    tr.Normalize = Normalize
    tv.transforms = tr
    sys.modules["torchvision"] = tv
    sys.modules["torchvision.transforms"] = tr


class _Models:
    """一次性加载 VAE/UNet/PE/Whisper/AudioProcessor。"""

    def __init__(self, device: str = "cuda", fp16: bool = True):
        import torch
        _setup_vendor_path()
        _install_torchvision_shim()
        from musetalk.models.vae import VAE
        from musetalk.models.unet import UNet, PositionalEncoding
        from musetalk.utils.audio_processor import AudioProcessor
        from transformers import WhisperModel

        mp = _model_paths()
        self.device = torch.device(device if (device == "cpu" or torch.cuda.is_available()) else "cpu")
        self.fp16 = bool(fp16 and self.device.type == "cuda")
        self.dtype = torch.float16 if self.fp16 else torch.float32

        t0 = time.time()
        self.vae = VAE(model_path=str(mp["vae"]), resized_img=256)
        self.vae.vae = self.vae.vae.to(self.device)
        self.unet = UNet(unet_config=str(mp["unet_config"]), model_path=str(mp["unet"]), device=self.device)
        self.pe = PositionalEncoding(d_model=384).to(self.device)
        if self.fp16:
            self.vae.vae = self.vae.vae.half()
            self.unet.model = self.unet.model.half()
            self.pe = self.pe.half()
        self.timesteps = torch.tensor([0], device=self.device)
        self.whisper = WhisperModel.from_pretrained(str(mp["whisper"]))
        self.whisper = self.whisper.to(device=self.device, dtype=self.dtype).eval()
        self.whisper.requires_grad_(False)
        self.audio_processor = AudioProcessor(feature_extractor_path=str(mp["whisper"]))
        self.load_seconds = round(time.time() - t0, 2)
        self.mem_after_load_mb = _gpu_mem_mb()

    @property
    def unet_dtype(self):
        return self.unet.model.dtype

    def whisper_chunks(self, wav_path, fps: float):
        feats, librosa_len = self.audio_processor.get_audio_feature(str(wav_path))
        return self.audio_processor.get_whisper_chunk(
            feats, self.device, self.dtype, self.whisper, librosa_len,
            fps=int(round(fps)), audio_padding_length_left=PADDING_LEFT,
            audio_padding_length_right=PADDING_RIGHT)


def _gpu_mem_mb() -> dict | None:
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        free, total = torch.cuda.mem_get_info()
        return {"used_mb": round((total - free) / 1048576, 1),
                "peak_alloc_mb": round(torch.cuda.max_memory_allocated() / 1048576, 1),
                "total_mb": round(total / 1048576, 1)}
    except Exception:
        return None


def _ffmpeg_frames_in(clip: str) -> list:
    """用 cv2 顺序解码整段视频，返回 (frames, fps)。"""
    import cv2
    cap = cv2.VideoCapture(str(clip))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {clip}")
    fps = cap.get(cv2.CAP_PROP_FPS) or FPS_FALLBACK
    frames = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        frames.append(fr)
    cap.release()
    return frames, float(fps)


def _write_video(frames: list, fps: float, audio_wav: str | Path, out_mp4: str | Path,
                 *, crf: int = 16) -> dict:
    """ffmpeg 重封装：rawvideo 管道输入 + 音频 track 复用。"""
    fps = float(fps) or FPS_FALLBACK
    h, w = frames[0].shape[:2]
    cmd = [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", f"{fps:.6f}", "-i", "-",
           "-i", str(audio_wav),
           "-map", "0:v:0", "-map", "1:a:0",
           "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
           str(out_mp4)]
    t0 = time.time()
    proc = subprocess.Popen([str(c) for c in cmd], stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for fr in frames:
            proc.stdin.write(fr.tobytes())
        proc.stdin.close()
    except Exception:
        proc.kill()
        raise
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg 编码失败 rc={rc}: {proc.stderr.read().decode('utf-8', 'replace')[-800:]}")
    return {"encode_seconds": round(time.time() - t0, 2),
            "encoder": "libx264", "crf": crf, "fps": fps, "size": [w, h]}


def _cut_audio(audio_wav: str | Path, start: float, duration: float, dst: Path) -> Path:
    dur = f"{duration:.6f}" if duration else None
    cmd = [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.6f}",
           "-i", str(audio_wav)]
    if dur:
        cmd += ["-t", dur]
    cmd += ["-c:a", "pcm_s16le", str(dst)]
    _run(cmd)
    return dst


def _prepare_frames(frames, device, audio_wav, fps, *, face_hint=None, max_frames=None,
                    lock_radius: float = 1.6, max_lost: int = 20,
                    skip_after_lost: int = 3):
    """逐帧检测 + crop 信息，返回 (coords, detect_stats, valid)。

    跟踪策略（防“串脸”：把别的角色当说话人）：
    - 首帧锁最大脸；之后只在上一帧脸附近（center 距离 < lock_radius*脸宽）选候选；
    - 目标丢失时沿用上一帧 crop（不切人）；连续丢失 > skip_after_lost 帧标记 valid=False
      （集成侧不贴回，避免把旧脸贴到背景）；连续丢失 > max_lost 帧才重新锁定；
    - face_hint 给定时优先 hint 附近的脸（多角色同框且已知说话人站位）。
    """
    t0 = time.time()
    detect_times = []
    raw_boxes = []
    valid = []
    locked = None
    lost = 0
    found = 0
    for fr in frames:
        t = time.perf_counter()
        faces = detect_faces(fr)
        detect_times.append(time.perf_counter() - t)
        face = None
        if face_hint is not None:
            face = pick_face(faces, None, hint=face_hint)
        elif locked is None:
            face = faces[0] if faces else None
            if face is not None:
                lost = 0
        else:
            lw = max(1.0, locked[2] - locked[0])
            lcx, lcy = (locked[0] + locked[2]) / 2, (locked[1] + locked[3]) / 2
            cands = [d for d in faces
                     if ((d["bbox"][0] + d["bbox"][2]) / 2 - lcx) ** 2 +
                        ((d["bbox"][1] + d["bbox"][3]) / 2 - lcy) ** 2 <= (lock_radius * lw) ** 2]
            if cands:
                face = max(cands, key=lambda d: (d["bbox"][2] - d["bbox"][0]) *
                           (d["bbox"][3] - d["bbox"][1]))
        if face is None:
            lost += 1
            raw_boxes.append(None)          # 沿用上一帧 crop（_smooth_bboxes 处理）
            valid.append(lost <= skip_after_lost)
            if locked is not None and lost > max_lost:
                locked = None               # 长时间丢失才允许重新锁人
            continue
        lost = 0
        found += 1
        locked = face["bbox"]
        raw_boxes.append(bbox_to_crop(face["bbox"], face["landmarks"],
                                      frame_hw=fr.shape[:2]))
        valid.append(True)
    coords = _smooth_bboxes(raw_boxes)
    detect_stats = {
        "face_found_frames": found,
        "frames": len(frames),
        "face_found": found >= max(1, int(0.5 * len(frames))),
        "valid_frames": sum(1 for v in valid if v),
        "detect_seconds": round(time.time() - t0, 2),
        "detect_ms_per_frame": round(1000 * sum(detect_times) / max(1, len(detect_times)), 2),
    }
    return coords, detect_stats, valid


def _musetalk_infer(frames, coords, audio_wav, fps, device, *, fp16=True,
                    dump_dir: Path | None = None, dump_frames=(),
                    mask_params: dict | None = None, valid=None) -> dict:
    """MuseTalk 1.5 主推理：VAE encode → whisper → unet 单步 → VAE decode → 掩码贴回。"""
    import cv2
    import numpy as np
    import torch

    model = _Models(device=device, fp16=fp16)
    whisper_chunks = model.whisper_chunks(audio_wav, fps)
    n = min(len(frames), len(coords), int(whisper_chunks.shape[0]))
    if n <= 0:
        raise RuntimeError(f"音频/视频帧数不匹配: frames={len(frames)} coords={len(coords)} "
                           f"whisper={int(whisper_chunks.shape[0])}")

    # ---- 1) 逐帧 crop + VAE encode ----
    t_pre = time.time()
    latents = []
    crops_meta = []
    for i in range(n):
        fr = frames[i]
        cb = coords[i]
        if cb is None:
            cb = (int(fr.shape[1] * 0.3), int(fr.shape[0] * 0.2),
                  int(fr.shape[1] * 0.7), int(fr.shape[0] * 0.8))
        x1, y1, x2, y2 = [int(v) for v in cb]
        x1 = max(0, min(x1, fr.shape[1] - 2)); x2 = max(x1 + 2, min(x2, fr.shape[1]))
        y1 = max(0, min(y1, fr.shape[0] - 2)); y2 = max(y1 + 2, min(y2, fr.shape[0]))
        crop = fr[y1:y2, x1:x2]
        crop256 = cv2.resize(crop, (256, 256), interpolation=cv2.INTER_LANCZOS4)
        with torch.no_grad():
            latents.append(model.vae.get_latents_for_unet(crop256))
        crops_meta.append((x1, y1, x2, y2))
    preprocess_s = round(time.time() - t_pre, 3)

    # ---- 2) UNet 单步 + VAE decode（batch）----
    t_inf = time.time()
    generated = [None] * n
    unet_times = []
    batch = BATCH_SIZE
    for s in range(0, n, batch):
        e = min(n, s + batch)
        wb = whisper_chunks[s:e].to(model.device)
        lb = torch.cat(latents[s:e], dim=0).to(device=model.device, dtype=model.unet_dtype)
        t = time.perf_counter()
        with torch.no_grad():
            audio_feat = model.pe(wb)
            pred = model.unet.model(lb, model.timesteps, encoder_hidden_states=audio_feat).sample
            recon = model.vae.decode_latents(pred)          # [b,256,256,3] BGR uint8
        torch.cuda.synchronize() if torch.cuda.is_available() else None
        unet_times.append(time.perf_counter() - t)
        for j in range(e - s):
            generated[s + j] = recon[j]
    infer_s = round(time.time() - t_inf, 3)

    # ---- 3) 贴回 ----
    t_paste = time.time()
    out_frames = []
    skipped = 0
    for i in range(n):
        fr = frames[i]
        if valid is not None and i < len(valid) and not valid[i]:
            out_frames.append(fr)      # 目标长时间丢失：不贴回，保留原帧
            skipped += 1
            continue
        x1, y1, x2, y2 = crops_meta[i]
        out_frames.append(paste_face(fr, generated[i], (x1, y1, x2, y2),
                                     mask_params=mask_params))
    paste_s = round(time.time() - t_paste, 3)

    if dump_dir is not None:
        dump_dir = Path(dump_dir)
        dump_dir.mkdir(parents=True, exist_ok=True)
        for idx in dump_frames:
            if 0 <= idx < n:
                cv2.imwrite(str(dump_dir / f"{idx:04d}_before.png"), frames[idx])
                cv2.imwrite(str(dump_dir / f"{idx:04d}_after.png"), out_frames[idx])

    mem = _gpu_mem_mb()
    return {
        "frames": n,
        "out_frames": out_frames,
        "crops": crops_meta,
        "skipped_paste_frames": skipped,
        "timing": {
            "model_load_s": model.load_seconds,
            "preprocess_vae_s": preprocess_s,
            "unet_decode_s": infer_s,
            "paste_s": paste_s,
            "unet_ms_per_batch": [round(x * 1000, 1) for x in unet_times],
            "per_frame_ms": round(1000 * (preprocess_s + infer_s + paste_s) / n, 2),
            "gpu": mem,
        },
    }


# --------------------------------------------------------------------------- #
# 公开 API
# --------------------------------------------------------------------------- #
def _sync_in_process(clip, audio, out, *, provider, device, workdir, start, duration,
                     face_hint, dump_frames, fp16, mask_params=None) -> dict:
    import cv2
    t_all = time.time()
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    info_in = probe(clip)
    workdir = Path(workdir) if workdir else _DEFAULT_WORK / "runtime"
    workdir.mkdir(parents=True, exist_ok=True)
    tag = Path(clip).stem

    # 音频片段（与视频段对齐）
    audio_seg = _cut_audio(audio, start or 0.0, duration or 0.0, workdir / f"{tag}_audio_seg.wav")

    frames, vfps = _ffmpeg_frames_in(clip)
    if start or duration:
        s = int(round((start or 0.0) * vfps))
        e = len(frames) if not duration else min(len(frames), s + int(round(duration * vfps)))
        frames = frames[s:e]
    fps = vfps or info_in["fps"]

    coords, det_stats, valid = _prepare_frames(frames, device, audio_seg, fps, face_hint=face_hint)

    import torch
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.empty_cache()
    res = _musetalk_infer(frames, coords, audio_seg, fps, device, fp16=fp16,
                          dump_dir=workdir / f"{tag}_dump", dump_frames=dump_frames,
                          mask_params=mask_params, valid=valid)
    enc = _write_video(res["out_frames"], fps, audio_seg, out)
    info_out = probe(out)
    total_s = round(time.time() - t_all, 2)
    gpu = res["timing"].get("gpu") or {}
    before_after = [str(workdir / f"{tag}_dump" / f"{i:04d}_{k}.png")
                    for i in dump_frames for k in ("before", "after")]
    return {
        "ok": True,
        "provider": provider,
        # ---- V5_INTERFACES §5 契约字段 ----
        "frames": res["frames"],
        "elapsed_sec": total_s,
        "vram_peak_mb": gpu.get("peak_alloc_mb"),
        "face_found": det_stats["face_found"],
        "before": before_after[0] if before_after else None,
        "after": before_after[1] if len(before_after) > 1 else None,
        # ---- 扩展字段（调试/报告用）----
        "clip": str(clip),
        "audio": str(audio),
        "out": str(out),
        "device": device,
        "fp16": bool(fp16),
        "input": {"width": info_in["width"], "height": info_in["height"],
                  "fps": info_in["fps"], "frames": info_in["n_frames"]},
        "output": {"width": info_out["width"], "height": info_out["height"],
                   "fps": info_out["fps"], "frames": info_out["n_frames"],
                   "duration": info_out["duration"]},
        "processed_frames": res["frames"],
        "skipped_paste_frames": res.get("skipped_paste_frames", 0),
        "detect": det_stats,
        "timing": {**res["timing"], **enc,
                   "total_s": total_s,
                   "fps_throughput": round(res["frames"] / total_s, 2) if total_s else None},
        "mask": {**_MASK_DEFAULTS, **(mask_params or {})},
        "dump_dir": str(workdir / f"{tag}_dump"),
        "crops": res["crops"],
        "workdir": str(workdir),
        "frames_found": len(res["out_frames"]),
        "before_after": [str(workdir / f"{tag}_dump" / f"{i:04d}_{k}.png")
                         for i in dump_frames for k in ("before", "after")],
    }


def sync_clip(clip_mp4, audio_wav, out_mp4, *, provider="musetalk", device="cuda",
              workdir=None, start=None, duration=None, face_hint=None,
              dump_frames=(0,), fp16=True, max_frames: int = 600,
              mask_params: dict | None = None) -> dict:
    """给单镜头视频对口型。

    Args:
        clip_mp4: 输入镜头（H3 输出或归一化后片段）
        audio_wav: 该镜头对应音频（TTS wav），与视频对齐
        out_mp4: 输出路径
        provider: "musetalk"（当前唯一可用）
        device: "cuda" / "cpu"
        workdir: 临时目录，默认 tmp/v5_lipsync/runtime
        start/duration: 只处理视频的一段（秒）
        face_hint: (x, y) 说话人脸大致位置，用于多脸场景
        dump_frames: 需要导出前/后对比帧的索引
        max_frames: 安全上限（默认 600 帧 ≈ 24s@25fps），超过直接报错，防误跑长批量
    Returns:
        dict: provider/耗时/face_found/输入输出信息/前后帧路径

    NOTE: 调用方必须串行调用（一次一个 clip），不要循环并发 spawn。
    """
    if provider != "musetalk":
        raise ValueError(f"unsupported provider: {provider}")
    Path(out_mp4).parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(workdir) if workdir else _DEFAULT_WORK / "runtime"

    info = probe(clip_mp4)
    n_est = info["n_frames"] if not duration else int(round(duration * info["fps"]))
    if n_est > max_frames:
        raise ValueError(f"clip 帧数 {n_est} 超过 max_frames={max_frames}（防误跑长批量）")

    # ---- 转发闸：venv 内就地跑；主环境只转发一次，绝不允许链式递归 ----
    depth = int(os.environ.get("LIPSYNC_FORWARD_DEPTH", "0") or 0)
    in_proc = (_in_lipsync_venv() or _venv_ready()
               or os.environ.get("LIPSYNC_INPROC") == "1" or not VENV_PY.exists())
    if in_proc:
        missing = _missing_deps()
        if missing:
            raise RuntimeError(
                "当前解释器缺少 MuseTalk 依赖且禁止再转发（防进程风暴）："
                + ", ".join(missing) + f"\n请用 venv 运行: {VENV_PY}")
        return _sync_in_process(clip_mp4, audio_wav, out_mp4, provider=provider, device=device,
                                workdir=workdir, start=start, duration=duration,
                                face_hint=face_hint, dump_frames=dump_frames, fp16=fp16,
                                mask_params=mask_params)

    if depth >= 1:
        # 已在转发链中：绝不再次 spawn（历史进程风暴根因，见 docs §事故与修复）
        raise RuntimeError(
            f"lipsync 转发深度={depth}，拒绝再次 spawn；缺失依赖: {_missing_deps() or '未知'}")

    cmd = [str(VENV_PY), "-m", "pipeline.lipsync", "--clip", str(clip_mp4),
           "--audio", str(audio_wav), "--out", str(out_mp4), "--json",
           "--device", str(device), "--workdir", str(workdir)]
    if start:
        cmd += ["--start", f"{start:.6f}"]
    if duration:
        cmd += ["--duration", f"{duration:.6f}"]
    if face_hint:
        cmd += ["--face-hint", f"{face_hint[0]},{face_hint[1]}"]
    if dump_frames:
        cmd += ["--dump-frames", ",".join(str(i) for i in dump_frames)]
    if not fp16:
        cmd += ["--fp32"]
    if mask_params:
        cmd += ["--mask", ",".join(str(mask_params.get(k, _MASK_DEFAULTS[k]))
                                  for k in ("cy", "rx", "ry", "feather"))]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["LIPSYNC_FORWARD_DEPTH"] = str(depth + 1)
    env["LIPSYNC_INPROC"] = "1"          # 子进程必须就地跑，禁止再转发
    timeout = float(os.environ.get("LIPSYNC_TIMEOUT", "900"))
    try:
        proc = subprocess.run([str(c) for c in cmd], cwd=str(REPO), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=env,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"lipsync venv 执行超时（>{timeout:.0f}s），已终止子进程")
    if proc.returncode != 0:
        raise RuntimeError(f"lipsync venv 执行失败 rc={proc.returncode}\n"
                           f"STDERR tail:\n{(proc.stderr or '')[-2000:]}")
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    payload = None
    for ln in reversed(lines):
        try:
            payload = json.loads(ln)
            break
        except Exception:
            continue
    if payload is None:
        raise RuntimeError(f"无法解析 venv 输出:\n{(proc.stdout or '')[-2000:]}")
    return payload


def measure_sync(clip_mp4, audio_wav=None, *, device="cuda") -> dict:
    """SyncNet 同步度测量（可选能力，V5_INTERFACES §5 契约字段 lse_c/lse_d）。

    当前**未接入**（需要 xformers + LatentSync syncnet config；优先级低于主链路），
    返回 {"ok": False, "reason": ...}，调用方应视为"无同步度分数"并继续。
    """
    try:
        _setup_vendor_path()
        from musetalk.models.syncnet import SyncNet  # noqa: F401  (存在性检查)
        _ = SyncNet
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"syncnet 不可用: {type(exc).__name__}: {exc}"}
    return {"ok": False,
            "reason": "SyncNet 权重存在但同步度打分未接入（需 xformers + LatentSync syncnet config），"
                      "见 docs/LIPSYNC_FEASIBILITY.md §8"}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V5 lip-sync（MuseTalk 1.5 provider）")
    ap.add_argument("--clip", default=None)
    ap.add_argument("--audio", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--provider", default="musetalk")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--start", type=float, default=None)
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--face-hint", default=None, help="x,y")
    ap.add_argument("--dump-frames", default="0")
    ap.add_argument("--mask", default=None, help="cy,rx,ry,feather（贴回羽化椭圆掩码参数）")
    ap.add_argument("--fp32", action="store_true")
    ap.add_argument("--json", action="store_true", help="只输出 JSON 结果（供 subprocess 调用）")
    ap.add_argument("--check", action="store_true", help="打印 available()")
    args = ap.parse_args(argv)

    if args.check:
        print(json.dumps(available(), ensure_ascii=False, indent=2))
        return 0
    if not (args.clip and args.audio and args.out):
        ap.error("--clip/--audio/--out 必填（--check 除外）")

    hint = None
    if args.face_hint:
        a, b = args.face_hint.split(",")
        hint = (float(a), float(b))
    mask_params = None
    if args.mask:
        vals = [float(x) for x in args.mask.split(",")]
        if len(vals) != 4:
            ap.error("--mask 需要 4 个数字: cy,rx,ry,feather")
        mask_params = dict(zip(("cy", "rx", "ry", "feather"), vals))
    dumps = tuple(int(x) for x in str(args.dump_frames).split(",") if x.strip() != "")

    res = sync_clip(args.clip, args.audio, args.out, provider=args.provider,
                    device=args.device, workdir=args.workdir, start=args.start,
                    duration=args.duration, face_hint=hint, dump_frames=dumps,
                    fp16=not args.fp32, mask_params=mask_params)
    if args.json:
        slim = {k: v for k, v in res.items() if k not in ("crops",)}
        print(json.dumps(slim, ensure_ascii=False))
    else:
        print(json.dumps({k: v for k, v in res.items() if k != "crops"},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
