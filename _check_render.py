"""检查 Render 部署状态 + Resend 邮件测试。"""
import requests, json

BASE = "https://resume-editor-h0d0.onrender.com"
ADMIN_PWD = "rcsz123456"

print("=== Render 部署状态检查 ===\n")

# 1. Health check
try:
    r = requests.get(f"{BASE}/health", timeout=30)
    print(f"[1] /health → {r.status_code} {'✅' if r.ok else '❌'}")
except Exception as e:
    print(f"[1] /health → ❌ {e}")
    print("    Render 可能还在冷启动")
    exit(0)

# 2. admin 登录
s = requests.Session()
r = s.post(f"{BASE}/api/admin/login", json={"password": ADMIN_PWD}, timeout=15)
print(f"[2] admin 登录 → {r.status_code} {'✅' if r.ok else '❌'}")

# 3. 跑 test-smtp（现在用 Resend）
print("[3] Resend 邮件连通性测试...", flush=True)
r = s.get(f"{BASE}/api/admin/test-smtp", timeout=30)
print(f"    HTTP status: {r.status_code}")
if r.ok:
    d = r.json()
    print(f"    configured: {d.get('configured')}")
    print(f"    success: {d.get('success')}")
    for step in d.get("steps", []):
        print(f"    {step}")
    if d.get("error"):
        print(f"    ⚠️ error: {d['error']}")
else:
    print(f"    ❌ body: {r.text[:300]}")

# 4. 跑一次真实回复 → 触发邮件
print("\n[4] 真实触发：重发一封测试邮件...")
r = requests.post(f"{BASE}/api/feedback/submit", json={
    "fingerprint": "check_" + "z" * 20,
    "content": "这是一封检查邮件，收到就好",
    "contact": "XGTX20020526@163.com",
    "image_base64": "",
}, timeout=15)
fid = r.json()["feedback"]["id"]
print(f"    新工单 fid={fid[:8]}")

r = s.post(f"{BASE}/api/admin/feedbacks/{fid}/reply", json={
    "reply": "👋 你好！这是 Resend 邮件通知系统的测试邮件。"
}, timeout=15)
print(f"    回复工单 → {r.status_code} body={r.json()}")
