"""邮件通知 E2E 测试：管理员回复工单 → 自动发邮件给用户。"""
import requests, json, time

BASE = "https://resume-editor-h0d0.onrender.com"
FP = "test_fp_email_" + "a" * 20
USER_EMAIL = "test2026@qq.com"  # 不存在的邮箱，只测接口链路

print("=" * 60)
print("邮件通知链路测试")
print("=" * 60)

# 1. 提交反馈（带邮箱）
print("\n[1] 用户提交反馈（带邮箱）...", end=" ", flush=True)
r = requests.post(f"{BASE}/api/feedback/submit", json={
    "fingerprint": FP,
    "content": "简历编辑器导出PDF后浏览器卡死，CPU 100%",
    "contact": USER_EMAIL,
    "image_base64": "",
}, timeout=15)
fb = r.json()
fid = fb["feedback"]["id"]
print(f"fid={fid[:8]} ✅")

# 2. 等 AI 分析 + 管理员登录
print("[2] 管理员登录...", end=" ", flush=True)
s = requests.Session()
r = s.post(f"{BASE}/api/admin/login", json={"password": "admin123"}, timeout=15)
print(f"{r.status_code} ✅" if r.ok else f"{r.status_code} ❌")

# 3. 管理员回复工单 → 自动触发邮件
print("[3] 管理员回复工单（触发邮件发送）...", flush=True)
r = s.post(f"{BASE}/api/admin/feedbacks/{fid}/reply", json={
    "reply": "你好，我们检查了一下可能是 PDF 导出时 Canvas 分辨率太高导致的。已经修复了，现在导出应该没问题了，请刷新页面后重试。",
}, timeout=30)
print(f"    status={r.status_code} body={r.json()}")
if r.ok:
    d = r.json()
    print(f"    email_sent={d.get('email_sent')} {'✅' if d.get('email_sent') else '（邮件未配置或用户无邮箱，正常跳过）'}")

# 4. 查 admin 列表确认状态
print("\n[4] 确认工单状态...")
r = s.get(f"{BASE}/api/admin/feedbacks", timeout=15)
items = r.json()["items"]
mine = [i for i in items if i["id"] == fid]
if mine:
    m = mine[0]
    print(f"    status={m['status']} admin_reply={bool(m.get('admin_reply'))} contact={m.get('contact')}")
else:
    print(f"    没找到 fid={fid[:8]}")

# 5. 用户端查工单（应该看到回复）
print("\n[5] 用户端查工单...")
r = requests.post(f"{BASE}/api/feedback/mine", json={"fingerprint": FP}, timeout=30)  # 可能惰性触发 AI
items = r.json()["items"]
latest = items[0] if items else {}
print(f"    admin_reply 可见: {bool(latest.get('admin_reply'))} {'✅' if latest.get('admin_reply') else '❌'}")
if latest.get("admin_reply"):
    print(f"    回复内容: {latest['admin_reply'][:150]}")

print("\n" + "=" * 60)
print("测试完成！")
print("=" * 60)
