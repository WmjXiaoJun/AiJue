import io
from pathlib import Path
anchors = {
 "pipeline/render_v5.py": [299,300,303,337,346,351,657,672,780,783,837,846,861,920,963,1151,1162,1247,1255],
 "pipeline/templates3d_hq.py": [515,527,531,533,571,590,731,735,812,897,932,953,967,970,980,999],
 "pipeline/tts_breeze.py": [301,319,321,353,357,358],
 "pipeline/v5_voices.py": [80,86,87,278,289],
 "pipeline/v5_audio.py": [389,394,398,401],
 "pipeline/v5_scene_art.py": [620,625,629,736],
 "pipeline/scene_render.py": [520,528,553,556],
 "pipeline/lipsync.py": [511,518,523,743,1058,1122,1167],
 "pipeline/qc_v5.py": [60,71,112,136,145,155,159],
 "pipeline/mouth_sync.py": [80,89],
 "pipeline/utils.py": [55,57,144,148],
 "pipeline/ass_builder.py": [147,158,160,230,231],
 "pipeline/templates3d_relip.py": [97,99,107],
 "pipeline/templates3d.py": [432,437,510,521,541],
 "pipeline/h3_v5.py": [64,66,82,830],
 "pipeline/h3_comfy.py": [593,594],
 "pipeline/motion2d.py": [905,916,920],
 "pipeline/postfx.py": [38,46,47],
 "pipeline/muse_video.py": [112,118,129,131,402,405],
 "pipeline/agents/registry.py": [350,353,356,359],
}
for f, lines in anchors.items():
    p = Path(f)
    if not p.is_file():
        print(f"!! {f} missing"); continue
    src = p.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"===== {f} ({len(src)}L) =====")
    for n in lines:
        if 1 <= n <= len(src):
            print(f"  {n:5d}| {src[n-1].rstrip()}")
