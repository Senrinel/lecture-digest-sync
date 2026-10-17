import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

html_path = Path("src/lecture_pipeline/gui/templates/index.html")
html = html_path.read_text(encoding="utf-8")

inputs = re.findall(r'<input\b([^>]*)>', html, re.IGNORECASE)
selects = re.findall(r'<select\b([^>]*)>', html, re.IGNORECASE)
print(f"Total inputs: {len(inputs)}, Total selects: {len(selects)}")
for idx, inp in enumerate(inputs, 1):
    id_m = re.search(r'id=["\']([^"\']+)["\']', inp)
    type_m = re.search(r'type=["\']([^"\']+)["\']', inp)
    onchange_m = re.search(r'onchange=["\']([^"\']+)["\']', inp)
    oninput_m = re.search(r'oninput=["\']([^"\']+)["\']', inp)
    i_id = id_m.group(1) if id_m else ""
    t_val = type_m.group(1) if type_m else "text"
    handler = oninput_m.group(1) if oninput_m else (onchange_m.group(1) if onchange_m else "")
    print(f"Input {idx:02d}: type={t_val:10} id={i_id:25} handler={handler[:30]}")

for idx, sel in enumerate(selects, 1):
    id_m = re.search(r'id=["\']([^"\']+)["\']', sel)
    onchange_m = re.search(r'onchange=["\']([^"\']+)["\']', sel)
    s_id = id_m.group(1) if id_m else ""
    handler = onchange_m.group(1) if onchange_m else ""
    print(f"Select {idx:02d}: id={s_id:25} handler={handler[:30]}")
