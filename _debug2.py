"""Debug with full session."""
import requests

BASE = "https://resume-editor-h0d0.onrender.com"

s = requests.Session()

# login
r = s.post(f"{BASE}/api/admin/login", json={"password": "admin123"}, timeout=15)
print(f"login: {r.status_code} cookies={dict(s.cookies)}")

# 列工单
r = s.get(f"{BASE}/api/admin/feedbacks", timeout=15)
print(f"list: {r.status_code}")
if r.ok:
    items = r.json()["items"]
    print(f"  总工单 {len(items)}")
    if items:
        fid = items[0]["id"]
        print(f"  回复最新工单 fid={fid[:8]} contact={items[0].get('contact','')}")
        r = s.post(f"{BASE}/api/admin/feedbacks/{fid}/reply", json={
            "reply": "debug reply - 检查邮件通知"
        }, timeout=30)
        print(f"  reply: status={r.status_code} body={r.text[:300]}")
