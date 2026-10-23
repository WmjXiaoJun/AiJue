# -*- coding: utf-8 -*-
"""Dev utility: tolerant dump of UI workflows (old+new) with widgets_values_named."""
import json, sys, os

def slot_name(s):
    if isinstance(s, dict): return s.get("name")
    if isinstance(s, (list, tuple)):
        return s[0] if s else None
    return str(s)

def links_of(o):
    if isinstance(o, dict): return o.get("links")
    if isinstance(o, (list, tuple)):
        return o[-1] if len(o) > 3 else None
    return None

def dump_workflow(path):
    d = json.load(open(path, encoding="utf-8"))
    nodes = d.get("nodes") or []
    links = d.get("links") or []
    print("#####", os.path.basename(path), "| nodes:", len(nodes), "links:", len(links))
    for n in nodes:
        print("  id=%s type=%s title=%r mode=%s" % (n.get("id"), n.get("type"), n.get("title"), n.get("mode")))
        print("     widgets_values=" + json.dumps(n.get("widgets_values"), ensure_ascii=False)[:600])
        print("     widgets_values_named=" + json.dumps(n.get("widgets_values_named"), ensure_ascii=False))
        print("     inputs=" + str([(slot_name(i), (i.get("link") if isinstance(i, dict) else None)) for i in (n.get("inputs") or [])]))
        print("     outputs=" + str([(slot_name(o), links_of(o)) for o in (n.get("outputs") or [])]))
    print("  links=" + json.dumps(links))

if __name__ == "__main__":
    for p in sys.argv[1:]:
        dump_workflow(p)
