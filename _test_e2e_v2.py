"""端到端测试 v2：反馈 + 截图 + AI 分析 + 修复方案 + 每日看板。"""
import requests, json, time

BASE = "https://resume-editor-h0d0.onrender.com"
FP = "test_fingerprint_e2e_v2_" + "b" * 20
IMG_BASE64 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+P+/HgAFhAJ/wlseKgAAAABJRU5ErkJggg=="  # 1x1 像素

print("=" * 60)
print("AI 客服 v2 端到端测试（截图 + AI 分析 + 修复方案 + 每日看板）")
print("=" * 60)

# 1. health
r = requests.get(f"{BASE}/health", timeout=15)
print(f"\n[1] /health → {r.status_code} ✅" if r.ok else f"\n[1] /health → {r.status_code} ❌")

# 2. admin 登录
s = requests.Session()
r = s.post(f"{BASE}/api/admin/login", json={"password": "admin123"}, timeout=15)
print(f"[2] admin 登录 → {r.status_code} {'✅' if r.ok else '❌'}")

# 3. admin 先刷一次旧数据（确保新字段 SELECT 不报错）
r = s.get(f"{BASE}/api/admin/feedbacks", timeout=15)
print(f"[3] admin 列工单 → {r.status_code} {'✅' if r.ok else '❌'}")
if r.ok:
    items = r.json().get("items", [])
    print(f"    旧工单 {len(items)} 条，每条是否有 ai_fix_plan 字段: {'ai_fix_plan' in items[0] if items else 'N/A'}")

# 4. 用户提交反馈（带截图）
payload = {
    "fingerprint": FP,
    "content": "简历编辑器导出 PDF 后在微信里打开是乱码，图片也不见了，怎么回事？",
    "contact": "test_user@e2e.com",
    "image_base64": IMG_BASE64,
}
r = requests.post(f"{BASE}/api/feedback/submit", json=payload, timeout=15)
print(f"\n[4] 用户提反馈（带截图）→ {r.status_code}")
try:
    fb = r.json()
    print(f"    ok={fb.get('ok')} ai_analyzing={fb.get('ai_analyzing')} msg={fb.get('message','')[:40]}")
    fid = fb.get("feedback", {}).get("id", "")
    print(f"    新工单 id={fid}")
except Exception:
    print(f"    body: {r.text[:300]}")
    fid = ""

# 5. 等 AI 分析完（最多等 60 秒，每 5 秒查一次）
print(f"\n[5] 等待 AI 分析完成...", end="", flush=True)
fb_final = None
for i in range(12):
    time.sleep(5)
    r = requests.post(f"{BASE}/api/feedback/mine", json={"fingerprint": FP}, timeout=15)
    items = r.json().get("items", [])
    latest = items[0] if items else {}
    if latest.get("ai_analysis"):
        fb_final = latest
        print(f" 完成（等了 {5*(i+1)}s）✅")
        break
    print(f" .", end="", flush=True)
if not fb_final:
    print(" 超时 ❌")

# 6. 用户端查工单（应该看到 ai_analysis，但**不应该**看到 ai_fix_plan）
print(f"\n[6] 用户端查工单...")
r = requests.post(f"{BASE}/api/feedback/mine", json={"fingerprint": FP}, timeout=15)
items = r.json().get("items", [])
latest = items[0] if items else {}
print(f"    有 ai_analysis: {bool(latest.get('ai_analysis'))} {'✅' if latest.get('ai_analysis') else '❌'}")
print(f"    有 ai_fix_plan: {bool(latest.get('ai_fix_plan', None))} {'✅' if 'ai_fix_plan' not in latest else '❌（不应该暴露给用户端）'}")
if latest.get("ai_analysis"):
    print(f"    AI 分析内容预览: {latest['ai_analysis'][:150]}")

# 7. admin 端查工单（应该同时看到 ai_analysis + ai_fix_plan）
print(f"\n[7] admin 端查工单...")
r = s.get(f"{BASE}/api/admin/feedbacks", timeout=15)
items = r.json().get("items", [])
# 找到刚才那条
mine = [i for i in items if i["id"] == fid] if fid else []
if mine:
    m = mine[0]
    print(f"    有 ai_analysis: {bool(m.get('ai_analysis'))}")
    print(f"    有 ai_fix_plan: {bool(m.get('ai_fix_plan'))} {'✅' if m.get('ai_fix_plan') else '⚠️（非 bug/UI/性能类可能没生成）'}")
    if m.get("ai_fix_plan"):
        print(f"    修复方案预览: {m['ai_fix_plan'][:200]}")
    print(f"    has_image: {m.get('has_image')} image_base64 长度: {len(m.get('image_base64',''))}")
else:
    print(f"    没找到新工单 fid={fid}，总工单 {len(items)} 条")

# 8. 每日看板
print(f"\n[8] 每日看板...")
r = s.get(f"{BASE}/api/admin/daily-report", timeout=15)
if r.ok:
    d = r.json()
    print(f"    日期={d['date']} 总量={d['total']} 待回复={d['pending']} 已回复={d['replied']} AI分析={d['ai_analyzed']} 带截图={d['with_image']}")
else:
    print(f"    失败 {r.status_code} {r.text[:200]}")

print("\n" + "=" * 60)
print("测试完成！")
print("=" * 60)
