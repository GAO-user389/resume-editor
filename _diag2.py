"""Resend 发送诊断：发到指定邮箱 + 查状态。"""
import requests, json, time

BASE = "https://resume-editor-h0d0.onrender.com"
ADMIN_PWD = "rcsz123456"
TARGET = "XGTX20020526@163.com"

print("=" * 60)
print("Resend 邮件发送诊断")
print("=" * 60)

# 1. 唤醒
print("\n[1] 唤醒 Render...", end=" ", flush=True)
requests.get(f"{BASE}/health", timeout=30)
print("OK")

# 2. admin 登录
s = requests.Session()
r = s.post(f"{BASE}/api/admin/login", json={"password": ADMIN_PWD}, timeout=15)
print(f"[2] admin 登录: {r.status_code}")

# 3. 让 test-smtp 直接发到 163（先临时改一下目标地址）
# 其实我们走 admin reply 路径——因为那条路径发的就是用户留的邮箱
print(f"\n[3] 提反馈 → 回复 → 发到 {TARGET}...")
FP = "diag_" + "d" * 20
r = requests.post(f"{BASE}/api/feedback/submit", json={
    "fingerprint": FP,
    "content": "Resend 诊断测试",
    "contact": TARGET,
    "image_base64": "",
}, timeout=15)
fid = r.json()["feedback"]["id"]
print(f"    fid={fid}")

r = s.post(f"{BASE}/api/admin/feedbacks/{fid}/reply", json={
    "reply": "🔍 诊断邮件 - 如果收到说明 Resend OK！"
}, timeout=15)
print(f"    reply → {r.json()}")

# 4. 等 5 秒让后台线程发完
print("\n[4] 等 5 秒让邮件发完...")
time.sleep(5)

# 5. 直接用 Resend API 查最近发的邮件状态
# 从 Render 环境变量里拿 API Key —— 我们可以让后端调 Resend 的 GET /emails
print("\n[5] 从 Render 后端查 Resend 邮件状态...")

# 先让 test-smtp 也发到 163（它默认发到 EMAIL_USER=onboarding@resend.dev，改一下发送目标）
# 我们直接用 httpx 从 Render 后端发起 Resend 发信 + 查询
# 加个临时端点？不，直接看 Resend Dashboard 或者从 Render 日志看

# 算了，让我们直接看看之前那封 test-smtp 发到 onboarding@resend.dev 的邮件结果
# Resend 返回了 id=01a1d39c-5a57-7e18-a1ea-65e5896cd6bc
# 我们没法直接查 Resend API（没有 key 在本地）
# 让我加个 /api/admin/resend-status 端点返回最近发的邮件状态

print("""
📋 你自己查 Resend 日志更快：
  1. 打开 https://resend.com/emails
  2. 应该能看到最近发的几封邮件
  3. 状态可能是: sent / delivered / bounced / failed

📋 或者查 Render Logs：
  1. Render Dashboard → resume-editor → Logs
  2. 搜 'Resend' 关键字
  3. 成功会有: Resend 发送成功 to=xxx id=xxx
  4. 失败会有: Resend HTTP 422/401 或异常堆栈

👉 最可能的原因：
   Resend onboarding@resend.dev 发件地址还没验证域名
   → 发给 Resend 自己的邮箱（onboarding@resend.dev）可能没问题
   → 发给 163/QQ 可能被当成垃圾邮件（因为发件地址没验证）
""")
