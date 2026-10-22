import re, json
data = open(r"E:\Robei\bin\Robei.exe", "rb").read()
out = []
for m in re.finditer(rb"\.vcd", data):
    s = max(0, m.start() - 120); e = min(len(data), m.end() + 120)
    chunk = data[s:e]
    txt = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
    out.append({"off": m.start(), "ctx": txt})
print(json.dumps(out, indent=1)[:6000])
