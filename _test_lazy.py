"""测试惰性 AI 分析触发路径（重点测 submit → 查 mine 时同步跑 AI）。"""
import requests, json, time

BASE = "https://resume-editor-h0d0.onrender.com"
FP = "test_fp_lazy_" + "x" * 20

# Step 1: 提交反馈
r = requests.post(f"{BASE}/api/feedback/submit", json={
    "fingerprint": FP,
    "content": "简历编辑器点导出PDF后弹出空白页，什么都没发生",
    "contact": "lazy@test.com",
    "image_base64": "",
}, timeout=15)
fb = r.json()
fid = fb["feedback"]["id"]
print(f"[1] 提交成功 fid={fid}")

# Step 2: 立即查 mine → 惰性触发 AI 分析（同步跑，可能要等 15-25s）
print("[2] 调 mine（惰性触发 AI 分析，约等 15-25s）...")
t0 = time.time()
r = requests.post(f"{BASE}/api/feedback/mine", json={"fingerprint": FP}, timeout=60)
elapsed = time.time() - t0
items = r.json()["items"]
latest = items[0] if items else {}
print(f"    耗时 {elapsed:.1f}s")
has_analysis = bool(latest.get("ai_analysis"))
has_fix_plan = "ai_fix_plan" in latest and bool(latest.get("ai_fix_plan"))
fix_plan_exposed = "ai_fix_plan" in latest
print(f"    ai_analysis: {has_analysis} {'✅' if has_analysis else '❌'}")
print(f"    ai_fix_plan 暴露给用户: {fix_plan_exposed} {'❌（不该暴露）' if fix_plan_exposed else '✅（已隐藏）'}")
if has_analysis:
    print(f"    AI 分析预览: {latest['ai_analysis'][:250]}")

# Step 3: admin 看（应该有 ai_fix_plan）
print("\n[3] admin 端登录查工单...")
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
    if m.get("ai_fix_plan"):
        print(f"    🔧 修复方案预览: {m['ai_fix_plan'][:300]}")
    elif f and not m.get("ai_fix_plan"):
        print(f"    （该反馈属于非 bug/UI/性能类，AI 未生成修复方案，正常）")
else:
    print(f"    没找到 fid={fid}，总工单 {len(items)}")

print("\n✅ 测试完成！")
