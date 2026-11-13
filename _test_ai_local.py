"""本地测试 AI 反馈分析函数，看具体报什么错。"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

# 设置环境变量指向 Render
os.environ.setdefault("AI_API_KEY", "zhipu_test_key")

from server.ai_client import call_llm, call_vision_ocr
from server import db
from server.main import _run_ai_feedback_analysis, _safe_parse_json, _format_ai_analysis, _generate_fix_plan

print("=== 测试 1: call_llm 连通性 ===")
try:
    r = call_llm("你好，回复一个 JSON: {\"ok\": true}")
    print(f"✅ call_llm 返回 (前100字): {r[:100]}")
except Exception as e:
    print(f"❌ call_llm 失败: {e}")

print("\n=== 测试 2: db 连通性 ===")
try:
    user = db.get_or_create_user("test_local_fp_" + "x" * 20)
    print(f"✅ db OK, user_id={user['id']}")
except Exception as e:
    print(f"❌ db 失败: {e}")

print("\n=== 测试 3: 完整 _run_ai_feedback_analysis ===")
try:
    # 先创建一条工单
    fb = db.submit_feedback(
        fingerprint="test_fp_run_" + "y" * 20,
        content="简历编辑器点导出PDF后弹出空白页",
        contact="test@local.com",
        image_base64="",
    )
    fid = fb["id"]
    print(f"工单已创建 fid={fid}")

    # 跑 AI 分析
    _run_ai_feedback_analysis(fid, "简历编辑器点导出PDF后弹出空白页", "")

    # 查结果
    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT ai_analysis, ai_fix_plan, ai_analyzed_at FROM feedbacks WHERE id = %s", (fid,))
        row = cur.fetchone()
        if row:
            print(f"ai_analysis: {row[0][:200] if row[0] else '(空)'}")
            print(f"ai_fix_plan: {row[1][:200] if row[1] else '(空)'}")
            print(f"ai_analyzed_at: {row[2]}")
except Exception as e:
    import traceback
    print(f"❌ 完整流程失败: {e}\n{traceback.format_exc()}")
