echo "=== MiniMaxH3ImageToVideo 输入定义 ==="
python3 - <<'PY'
import json, urllib.request
for c in ["MiniMaxH3ImageToVideo","LoraLoaderModelOnly","KSamplerSelect","MiniMaxH3AddGuide"]:
    try:
        d = json.load(urllib.request.urlopen("http://127.0.0.1:6006/object_info/" + c, timeout=15))
    except Exception as e:
        print(c, "ERR", e); continue
    i = d.get(c, {})
    inp = i.get("input", {})
    print("###", c, "| required:", list(inp.get("required", {}).keys()), "| optional:", list(inp.get("optional", {}).keys()))
    for k, v in list(inp.get("required", {}).items()) + list(inp.get("optional", {}).items()):
        if isinstance(v, list) and v:
            print("     %-18s type=%-22s %s" % (k, str(v[0])[:22], json.dumps(v[1], ensure_ascii=False)[:110] if len(v) > 1 else ""))
PY
