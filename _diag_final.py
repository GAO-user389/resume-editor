"""最终诊断：发一封邮件 + 直接打印每一步结果。"""
import requests, json, time

BASE = "https://resume-editor-h0d0.onrender.com"
ADMIN_PWD = "rcsz123456"
TARGET = "XGTX20020526@163.com"

print("=" * 60)
print("Resend 邮件最终诊断")
print("=" * 60)

# 唤醒
requests.get(f"{BASE}/health", timeout=30)

s = requests.Session()
s.post(f"{BASE}/api/admin/login", json={"password": ADMIN_PWD}, timeout=15)

# 直接同步发一封（不走后台线程），用 test-smtp 发到 163
# 先提一个反馈让 contact=163
print(f"\n[1] 提反馈 contact={TARGET}")
r = requests.post(f"{BASE}/api/feedback/submit", json={
    "fingerprint": "final2_" + "n" * 20,
    "content": "Resend 最终诊断 - 如果收到说明通了",
    "contact": TARGET,
    "image_base64": "",
}, timeout=15)
fid = r.json()["feedback"]["id"]
print(f"    fid={fid}")

# 同步 reply —— 但 reply 里邮件是后台线程发的，我们等它完
print(f"\n[2] admin reply → 触发后台线程发邮件...")
t0 = time.time()
r = s.post(f"{BASE}/api/admin/feedbacks/{fid}/reply", json={
    "reply": "🔔 这是一封从 Render 通过 Resend 发出的真实测试邮件。"
}, timeout=15)
print(f"    reply API 返回: {r.json()}")
print(f"    耗时: {time.time()-t0:.1f}s")

# 等后台线程
print(f"\n[3] 等 8 秒让后台线程跑完 Resend HTTP 调用...")
time.sleep(8)

# 现在让 test-smtp **同步** 发一封到 163（改一下临时让它发到 TARGET）
# 不行，test-smtp 发到 EMAIL_USER=onboarding@resend.dev
# 那我们再用 reply 路径发一次，这次我们加个 debug 端点返回 Resend 详细结果

# 让我再发一次 reply 试试
print(f"\n[4] 再 reply 一次，确保 Resend 被调用...")
fid2 = None
r = requests.post(f"{BASE}/api/feedback/submit", json={
    "fingerprint": "final3_" + "p" * 20,
    "content": "第二封测试",
    "contact": TARGET,
    "image_base64": "",
}, timeout=15)
fid2 = r.json()["feedback"]["id"]

r = s.post(f"{BASE}/api/admin/feedbacks/{fid2}/reply", json={
    "reply": "🔔 第二封测试邮件"
}, timeout=15)
print(f"    reply2 → {r.json()}")
time.sleep(5)

print(f"""
📋 现在去查：

1. Resend Dashboard → https://resend.com/emails
   - 往下滚动应该能看到最近的邮件
   - 找 TO 列是 "XGTX20020526@163.com" 的
   - 点进去看状态：delivered / sent / bounced / failed

2. 163 邮箱 → https://mail.163.com
   - 搜 "简历编辑器" 或 "客服回复"
   - 看垃圾箱/广告邮件

3. 如果 Resend 里只有发给 onboarding@resend.dev 的
   → 说明后台线程虽然启动了，但 httpx 调用 Resend 失败了
   → 去 Render Logs 搜 "Resend" 看错误信息
""")
