import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

html_path = Path("src/lecture_pipeline/gui/templates/index.html")
html = html_path.read_text(encoding="utf-8")

pattern = re.compile(r'<button\b([^>]*)>(.*?)</button>', re.DOTALL | re.IGNORECASE)
matches = pattern.findall(html)

print(f"Total buttons found: {len(matches)}")
print("=" * 80)
for idx, (attrs, body) in enumerate(matches, 1):
    body_clean = re.sub(r'<[^>]+>', '', body).strip().replace('\n', ' ')
    body_clean = re.sub(r'\s+', ' ', body_clean)
    id_m = re.search(r'id=["\']([^"\']+)["\']', attrs)
    onclick_m = re.search(r'onclick=["\']([^"\']+)["\']', attrs)
    disabled_m = "disabled" in attrs
    b_id = id_m.group(1) if id_m else ""
    onclick = onclick_m.group(1) if onclick_m else ""
    dis_str = "[DISABLED] " if disabled_m else ""
    print(f"{idx:02d} | {dis_str}id: {b_id:24} | onclick: {onclick:32} | {body_clean[:35]}")
