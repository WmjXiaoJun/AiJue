# -*- coding: utf-8 -*-
"""Dev utility: dump a UI workflow incl. widgets_values_named + fetch node schemas from /object_info."""
import json, sys, os, urllib.request

BASE = "http://127.0.0.1:8188"
NODES = ["UNETLoader","LoraLoaderModelOnly","MiniMaxH3SigmaShift","CLIPLoader","VAELoader",
         "MiniMaxH3ImageToVideo","MiniMaxH3ReferenceToVideo","BasicGuider","RandomNoise","KSamplerSelect",
         "BasicScheduler","SamplerCustomAdvanced","VAEDecodeTiled","VAEDecodeAudio","CreateVideo","SaveVideo","LoadImage"]

def dump_workflow(path):
    d = json.load(open(path, encoding="utf-8"))
    nodes = d.get("nodes") or []
    links = d.get("links") or []
    print("#####", os.path.basename(path), "| nodes:", len(nodes), "links:", len(links))
    for n in nodes:
        print("  id=%s type=%s title=%r mode=%s" % (n.get("id"), n.get("type"), n.get("title"), n.get("mode")))
        print("     widgets_values=" + json.dumps(n.get("widgets_values"), ensure_ascii=False))
        print("     widgets_values_named=" + json.dumps(n.get("widgets_values_named"), ensure_ascii=False))
        print("     inputs=" + str([(i.get("name"), i.get("link")) for i in (n.get("inputs") or [])]))
        print("     outputs=" + str([(o.get("name"), o.get("links")) for o in (n.get("outputs") or [])]))
    print("  links=" + json.dumps(links))

def dump_schema():
    info = json.load(urllib.request.urlopen(BASE + "/object_info", timeout=60))
    for name in NODES:
        if name not in info:
            print("MISSING NODE TYPE:", name); continue
        n = info[name]
        print("")
        print("#### " + name)
        print("  input_order=" + json.dumps(n.get("input_order")))
        print("  input=" + json.dumps(n.get("input"), ensure_ascii=False))
        print("  output=" + json.dumps(n.get("output")) + " name=" + json.dumps(n.get("output_name")))
        print("  category=" + str(n.get("category")) + " module=" + str(n.get("python_module")))

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "wf":
        for p in sys.argv[2:]:
            dump_workflow(p)
    elif cmd == "schema":
        dump_schema()
