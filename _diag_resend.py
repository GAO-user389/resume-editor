"""查 Render 上 Resend 邮件发送的真实日志结果。"""
import requests, json, time

BASE = "https://resume-editor-h0d0.onrender.com"
ADMIN_PWD = "rcsz123456"

# 1. 先唤醒 Render
print("唤醒 Render...")
for i in range(10):
    try:
        r = requests.get(f"{BASE}/health", timeout=15)
        if r.ok:
            print(f"OK (等了 {i*3}s)")
            break
    except Exception:
        pass
    time.sleep(3)

# 2. admin 登录 + test-smtp
s = requests.Session()
r = s.post(f"{BASE}/api/admin/login", json={"password": ADMIN_PWD}, timeout=15)
print(f"admin 登录: {r.status_code}")

r = s.get(f"{BASE}/api/admin/test-smtp", timeout=10)
print(f"test-smtp 触发: {r.status_code}")
d = r.json()
print(f"  resend_configured: {d.get('resend_configured')}")
print(f"  email_user: {d.get('email_user')}")

# 3. 后台线程发完后，用真实回复测一下并从返回日志侧面验证
print("\n等 5 秒让后台线程跑完...")
time.sleep(5)

# 4. 直接用 requests 库本地模拟 Resend 调用 —— 验证 API Key 对不对
print("\n=== 本地直接测 Resend API Key ===")
import os
import urllib.request

# 尝试从 Render 环境变量读（本地读不到），所以让我直接从 API 测试端点获取
# 或者让我打一个新端点返回 Resend 的实时测试结果
r = s.post(f"{BASE}/api/feedback/submit", json={
    "fingerprint": "diag_" + "a" * 20,
    "content": "诊断 Resend 邮件",
    "contact": "XGTX20020526@163.com",
    "image_base64": "",
}, timeout=15)
fid = r.json()["feedback"]["id"]
print(f"提反馈 fid={fid[:8]}")

r = s.post(f"{BASE}/api/admin/feedbacks/{fid}/reply", json={
    "reply": "诊断邮件 - 如果收到说明 Resend OK"
}, timeout=15)
print(f"回复 → {r.status_code} email_sent={r.json().get('email_sent')}")

print("\n📋 Render Logs 怎么看 Resend 结果：")
print("  Render Dashboard → resume-editor → Logs")
print("  搜关键字 'Resend 发送' 或 'test-smtp'")
print("  成功: Resend 发送成功 to=xxx id=xxx")
print("  失败: Resend HTTP 401/403/422 或异常堆栈")
