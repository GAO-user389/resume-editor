"""查 Render 上 Resend 邮件实际发给了谁。"""
import requests, json, time

BASE = "https://resume-editor-h0d0.onrender.com"
ADMIN_PWD = "rcsz123456"

print("=== Render 上邮件配置 + 最近发件记录 ===\n")

# 唤醒
requests.get(f"{BASE}/health", timeout=30)

s = requests.Session()
s.post(f"{BASE}/api/admin/login", json={"password": ADMIN_PWD}, timeout=15)

# 1. 查工单列表（看 contact 字段 = 收件人）
r = s.get(f"{BASE}/api/admin/feedbacks", timeout=15)
items = r.json()["items"]
print(f"最近 {min(6, len(items))} 条工单：")
print("-" * 70)
for m in items[:6]:
    contact = m.get("contact", "(空)")
    status = m.get("status", "")
    has_reply = bool(m.get("admin_reply"))
    print(f"  [{m['id'][:8]}] status={status} contact={contact:30s} reply={'✅' if has_reply else '❌'}")
print("-" * 70)

# 2. test-smtp 看当前 EMAIL_USER 是什么
r = s.get(f"{BASE}/api/admin/test-smtp", timeout=30)
d = r.json()
print(f"\n当前 Render 环境变量：")
print(f"  EMAIL_USER = {d.get('email_user')}  (发件人 from=这个)")
print(f"  RESEND_API_KEY 已配置: {d.get('resend_configured')}")
print(f"\ntest-smtp Resend 结果:")
print(f"  HTTP {d.get('resend_http_status')} → {d.get('resend_response', '')[:120]}")

# 3. 再发一封测试，发到 163 邮箱
print(f"\n=== 再发一封真正发到 163 的邮件 ===")
r = requests.post(f"{BASE}/api/feedback/submit", json={
    "fingerprint": "real_" + "m" * 20,
    "content": "最终确认：这封邮件应该发到 XGTX20020526@163.com",
    "contact": "XGTX20020526@163.com",  # ← 收件人是这里！
    "image_base64": "",
}, timeout=15)
fid = r.json()["feedback"]["id"]

# 查这单的 contact
r2 = s.get(f"{BASE}/api/admin/feedbacks", timeout=15)
all_items = r2.json()["items"]
this_one = [i for i in all_items if i["id"] == fid]
if this_one:
    print(f"工单 {fid[:8]} 的 contact（收件人）= {this_one[0].get('contact')}")

# 回复 → 触发发邮件到 contact（=163邮箱）
r = s.post(f"{BASE}/api/admin/feedbacks/{fid}/reply", json={
    "reply": "🎉 如果收到了，说明 Resend 全链路通了！"
}, timeout=15)
print(f"回复触发邮件: {r.json()}")

# 等 5 秒
print("\n等 5 秒让后台线程发完...")
time.sleep(5)

print(f"""
📋 总结：
  发件人 (from) = {d.get('email_user')}  (Render 上 EMAIL_USER)
  收件人 (to)   = XGTX20020526@163.com  (工单的 contact 字段)
  
  ✅ 配置对了的话，邮件应该到 163 邮箱
  ❌ 如果没到 → 去 Resend Dashboard 看 delivered 还是 bounced
  
👉 现在去查：https://mail.163.com （包括垃圾箱/广告邮件）
""")
