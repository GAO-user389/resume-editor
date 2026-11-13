"""直接测试 Render 上的 SMTP 能不能通。"""
import requests, json, time

BASE = "https://resume-editor-h0d0.onrender.com"
ADMIN_PWD = "rcsz123456"

# 1. 找一条最近被回复的工单（contact 应该有邮箱）
s = requests.Session()
s.post(f"{BASE}/api/admin/login", json={"password": ADMIN_PWD}, timeout=15)
r = s.get(f"{BASE}/api/admin/feedbacks", timeout=15)
items = r.json()["items"]
print(f"总工单 {len(items)} 条")
for m in items[:5]:
    print(f"  [{m['id'][:8]}] status={m['status']} contact={m.get('contact','(空)')} has_reply={bool(m.get('admin_reply'))}")

# 2. 找一条带邮箱且已回复的，再触发一次回复（强制重发邮件）
replied_with_email = [i for i in items if i["status"] == "replied" and i.get("contact") and "@" in i["contact"]]
if replied_with_email:
    m = replied_with_email[0]
    fid = m["id"]
    contact = m["contact"]
    print(f"\n重发邮件到 {contact}")
    r = s.post(f"{BASE}/api/admin/feedbacks/{fid}/reply", json={
        "reply": "【重发测试】这是一封重发测试邮件，请忽略。"
    }, timeout=10)
    print(f"reply status={r.status_code} body={r.text[:200]}")
