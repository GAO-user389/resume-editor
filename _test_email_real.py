"""测试真实邮件发送。"""
import requests, json, time

BASE = "https://resume-editor-h0d0.onrender.com"
FP = "test_fp_email3_" + "c" * 20
EMAIL = "XGTX20020526@163.com"

print("=== 邮件通知链路测试 ===\n")

# 1. 提反馈
r = requests.post(f"{BASE}/api/feedback/submit", json={
    "fingerprint": FP,
    "content": "请帮我确认一下PDF导出兼容性问题修好了吗",
    "contact": EMAIL,
    "image_base64": "",
}, timeout=15)
fid = r.json()["feedback"]["id"]
print(f"[1] 提交反馈 fid={fid[:8]}  contact={EMAIL}")

# 2. 管理员登录 + 回复
s = requests.Session()
s.post(f"{BASE}/api/admin/login", json={"password": "admin123"}, timeout=15)
print("[2] 管理员登录 OK")

# 3. 回复（触发邮件）
print("[3] 回复工单（触发邮件发送）...", flush=True)
t0 = time.time()
r = s.post(f"{BASE}/api/admin/feedbacks/{fid}/reply", json={
    "reply": "你好！PDF导出兼容性问题已经修复，现在导出的PDF在微信里打开也不会乱码了。请刷新页面后重试，有问题随时反馈～",
}, timeout=30)
elapsed = time.time() - t0
d = r.json()
ok = d.get("ok")
sent = d.get("email_sent")
print(f"    耗时 {elapsed:.1f}s  ok={ok}  email_sent={sent}")

if sent:
    print("    🎉 邮件已成功发送！检查一下你的邮箱（可能在垃圾箱里）")
else:
    print("    ⚠️ email_sent=False → Render 上 EMAIL_USER/EMAIL_PASSWORD 还没配好？")
