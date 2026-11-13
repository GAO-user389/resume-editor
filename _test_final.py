"""完整邮件通知测试（真实密码）。"""
import requests, json, time

BASE = "https://resume-editor-h0d0.onrender.com"
ADMIN_PWD = "rcsz123456"
FP = "test_fp_final_" + "e" * 20
EMAIL = "XGTX20020526@163.com"

print("=" * 60)
print("完整邮件通知链路测试")
print("=" * 60)

# 1. admin 登录
s = requests.Session()
r = s.post(f"{BASE}/api/admin/login", json={"password": ADMIN_PWD}, timeout=15)
print(f"\n[1] admin 登录 → {r.status_code} {'✅' if r.ok else '❌ ' + r.text[:100]}")

# 2. 用户提交反馈（带邮箱）
print("[2] 用户提交反馈...", end=" ", flush=True)
r = requests.post(f"{BASE}/api/feedback/submit", json={
    "fingerprint": FP,
    "content": "导出PDF后在手机上打开显示不全，右边内容被截断了",
    "contact": EMAIL,
    "image_base64": "",
}, timeout=15)
fb = r.json()
fid = fb["feedback"]["id"]
print(f"fid={fid[:8]} ✅")

# 3. 管理员回复 → 触发邮件
print("[3] 管理员回复（触发邮件发送）...", flush=True)
t0 = time.time()
r = s.post(f"{BASE}/api/admin/feedbacks/{fid}/reply", json={
    "reply": "你好！这个问题我们已经定位到原因了——移动端 PDF 渲染和桌面端不一样。我们刚刚更新了导出逻辑，现在 PDF 会自动适配移动端显示。请刷新页面后重新导出试试～",
}, timeout=30)
elapsed = time.time() - t0
print(f"    耗时 {elapsed:.1f}s")
print(f"    status={r.status_code} body={r.text[:300]}")

if r.ok:
    d = r.json()
    print(f"    ✅ ok={d.get('ok')}  email_sent={d.get('email_sent')}")
    if d.get("email_sent"):
        print("\n🎉🎉🎉 邮件发送成功！检查一下你的邮箱（包括垃圾箱/广告邮件）")
    else:
        print("\n⚠️  工单回复成功，但邮件没发出去 → Render 上 EMAIL_USER/EMAIL_PASSWORD 可能没配？")
else:
    print(f"    ❌ 回复失败: {r.text[:200]}")

# 4. 验证工单状态
print("\n[4] 确认工单状态...")
r = s.get(f"{BASE}/api/admin/feedbacks", timeout=15)
items = r.json()["items"]
mine = [i for i in items if i["id"] == fid]
if mine:
    m = mine[0]
    print(f"    status={m['status']}  has_admin_reply={bool(m.get('admin_reply'))}  contact={m.get('contact')}")

print("\n" + "=" * 60)
