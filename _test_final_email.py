"""最终 E2E：test-smtp + 真实回复触发邮件。"""
import requests, json, time

BASE = "https://resume-editor-h0d0.onrender.com"
ADMIN_PWD = "rcsz123456"

print("=" * 60)
print("Resend 邮件系统最终测试")
print("=" * 60)

# 1. 等 Render 起来
print("\n[1] 等 Render 完全起来...", end="", flush=True)
for i in range(10):
    try:
        r = requests.get(f"{BASE}/health", timeout=15)
        if r.ok:
            print(f" ✅ (等了 {i*3}s)")
            break
    except Exception:
        pass
    time.sleep(3)

# 2. admin 登录
s = requests.Session()
r = s.post(f"{BASE}/api/admin/login", json={"password": ADMIN_PWD}, timeout=15)
print(f"[2] admin 登录 → {r.status_code} {'✅' if r.ok else '❌'}")

# 3. test-smtp（后台线程，立即返回）
print("[3] test-smtp（后台发起 Resend 邮件）...", flush=True)
r = s.get(f"{BASE}/api/admin/test-smtp", timeout=10)
print(f"    status={r.status_code} body={json.dumps(r.json(), ensure_ascii=False)}")

# 4. 真实回复触发邮件（后台线程）
print("\n[4] 真实触发：提反馈 + 回复 → 自动发邮件...")
FP = "final_" + "q" * 20
EMAIL = "XGTX20020526@163.com"

r = requests.post(f"{BASE}/api/feedback/submit", json={
    "fingerprint": FP,
    "content": "这是一封最终邮件测试，收到就说明全流程通了！",
    "contact": EMAIL,
    "image_base64": "",
}, timeout=15)
fid = r.json()["feedback"]["id"]
print(f"    新工单 fid={fid[:8]}")

t0 = time.time()
r = s.post(f"{BASE}/api/admin/feedbacks/{fid}/reply", json={
    "reply": "🎉 恭喜！这是简历编辑器 Resend 邮件系统的最终测试邮件。如果收到了，说明整个客服系统闭环跑通了！"
}, timeout=15)
elapsed = time.time() - t0
print(f"    管理员回复 → status={r.status_code} 耗时 {elapsed:.1f}s  body={r.json()}")

print(f"\n{'=' * 60}")
print("✅ 测试完成！检查一下 XGTX20020526@163.com 有没有收到邮件")
print(f"{'=' * 60}")
