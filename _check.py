"""Check Render feedback state."""
import requests, json, time
BASE = "https://resume-editor-h0d0.onrender.com"

print("=== 检查 Render 上所有工单状态 ===\n")

s = requests.Session()
r = s.post(f"{BASE}/api/admin/login", json={"password": "admin123"}, timeout=15)
print(f"admin 登录: {r.status_code}")

r = s.get(f"{BASE}/api/admin/feedbacks", timeout=15)
items = r.json()["items"]
print(f"总工单 {len(items)} 条:\n")
for item in items[:8]:
    fid_short = item["id"][:8]
    st = item["status"]
    has_ana = bool(item.get("ai_analysis"))
    has_fix = bool(item.get("ai_fix_plan"))
    analyzed_at = item.get("ai_analyzed_at", "")
    content_preview = item["content"][:60]
    print(f"  [{fid_short}] status={st}")
    print(f"    ai_analysis={has_ana}  ai_fix_plan={has_fix}  ai_analyzed_at={analyzed_at}")
    print(f"    content: {content_preview}")
    print()
