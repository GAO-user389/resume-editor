"""完整 E2E v3 测试：惰性触发 AI 分析 + 后台线程 AI 分析。"""
import requests, json, time

BASE = "https://resume-editor-h0d0.onrender.com"
FP = "test_fp_v3_" + "z" * 20

print("=" * 60)
print("AI 客服 v3 完整 E2E 测试")
print("=" * 60)

# 0. 唤醒 Render
print("\n[0] 唤醒 Render...", end="", flush=True)
r = requests.get(f"{BASE}/health", timeout=30)
print(f" {r.status_code} {'✅' if r.ok else '❌'}")

# 1. 提交反馈（带后台线程触发）
print("\n[1] 提交反馈...", end=" ", flush=True)
r = requests.post(f"{BASE}/api/feedback/submit", json={
    "fingerprint": FP,
    "content": "简历编辑器导出 PDF 后在微信里打开是乱码，图片也不见了",
    "contact": "v3@test.com",
    "image_base64": "",
}, timeout=15)
fb = r.json()
fid = fb["feedback"]["id"]
print(f"fid={fid[:8]} ✅")

# 2. 惰性触发路径：立刻查 mine（同步跑 AI 分析，这次应该能成功）
print("[2] 调 mine（惰性触发 AI，约等 15-25s）...", flush=True)
t0 = time.time()
r = requests.post(f"{BASE}/api/feedback/mine", json={"fingerprint": FP}, timeout=60)
elapsed = time.time() - t0
items = r.json()["items"]
latest = items[0] if items else {}
ana = bool(latest.get("ai_analysis"))
fix_in_user = "ai_fix_plan" in latest
print(f"    耗时 {elapsed:.1f}s  ai_analysis={ana} {'✅' if ana else '❌'}")
print(f"    ai_fix_plan 不暴露给用户: {not fix_in_user} {'✅' if not fix_in_user else '❌'}")
if latest.get("ai_analysis"):
    print(f"    AI 分析:\n{latest['ai_analysis']}")

# 3. admin 看（应该同时有 analysis + fix_plan）
print("\n[3] admin 端登录查...")
s = requests.Session()
s.post(f"{BASE}/api/admin/login", json={"password": "admin123"}, timeout=15)
r = s.get(f"{BASE}/api/admin/feedbacks", timeout=15)
items = r.json()["items"]
mine = [i for i in items if i["id"] == fid]
if mine:
    m = mine[0]
    a = bool(m.get("ai_analysis"))
    f = bool(m.get("ai_fix_plan"))
    print(f"    ai_analysis={a} ai_fix_plan={f}")
    if m.get("ai_analysis"):
        print(f"    🤖 分析:\n    {m['ai_analysis'][:300]}")
    if m.get("ai_fix_plan"):
        print(f"    🔧 修复方案:\n    {m['ai_fix_plan'][:400]}")
    print(f"    ai_analyzed_at={m.get('ai_analyzed_at')}")
else:
    print(f"    没找到 fid={fid[:8]}，总工单 {len(items)}")

# 4. 每日看板
print("\n[4] 每日看板...")
r = s.get(f"{BASE}/api/admin/daily-report", timeout=15)
d = r.json()
print(f"    日期={d['date']} 总量={d['total']} 待回复={d['pending']} 已回复={d['replied']} AI分析={d['ai_analyzed']}")

print("\n" + "=" * 60)
print("测试完成！")
print("=" * 60)
