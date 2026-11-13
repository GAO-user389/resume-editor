# -*- coding: utf-8 -*-
"""
数据库操作模块（Supabase PostgreSQL）。
- 用户（设备指纹）管理
- 免费次数计数
- 激活码验证与核销
- 反馈工单系统
"""
import os
import time
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import psycopg2
from psycopg2.pool import SimpleConnectionPool

DSN = os.getenv("DATABASE_URL", "")

_pool: Optional[SimpleConnectionPool] = None
_pool_lock = threading.Lock()


def get_pool() -> SimpleConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                if not DSN:
                    raise RuntimeError("DATABASE_URL 未配置")
                _pool = SimpleConnectionPool(
                    minconn=1, maxconn=5, dsn=DSN,
                    connect_timeout=15,
                )
    return _pool


@contextmanager
def get_conn():
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# ========== 用户操作 ==========
def get_or_create_user(fingerprint: str) -> dict:
    """根据设备指纹获取或创建用户，返回用户信息。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, fingerprint, free_calls_used, is_premium, activation_code_id "
            "FROM users WHERE fingerprint = %s",
            (fingerprint,),
        )
        row = cur.fetchone()
        if row:
            return {
                "id": str(row[0]),
                "fingerprint": row[1],
                "free_calls_used": row[2],
                "is_premium": row[3],
                "activation_code_id": str(row[4]) if row[4] else None,
            }
        # 新建用户
        cur.execute(
            "INSERT INTO users (fingerprint) VALUES (%s) RETURNING id",
            (fingerprint,),
        )
        uid = str(cur.fetchone()[0])
        return {
            "id": uid,
            "fingerprint": fingerprint,
            "free_calls_used": 0,
            "is_premium": False,
            "activation_code_id": None,
        }


def increment_free_calls(user_id: str) -> int:
    """用户免费次数 +1，返回当前已用次数。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET free_calls_used = free_calls_used + 1, updated_at = NOW() "
            "WHERE id = %s RETURNING free_calls_used",
            (user_id,),
        )
        row = cur.fetchone()
        return row[0] if row else 0


def get_free_calls_remaining(user_id: str, total_free: int) -> int:
    """获取剩余免费次数。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT free_calls_used, is_premium FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if not row:
            return total_free
        used, is_premium = row
        if is_premium:
            return -1  # -1 表示无限
        return max(0, total_free - used)


# ========== 激活码操作 ==========
def verify_and_use_code(code: str, user_id: str) -> bool:
    """验证激活码并核销（绑定到用户）。成功返回 True。"""
    code = code.strip().upper()
    with get_conn() as conn:
        cur = conn.cursor()
        # 条件更新：只更新未使用的激活码（天然防并发，兼容 pooler）
        cur.execute(
            "UPDATE activation_codes SET is_used = TRUE, used_by = %s, used_at = NOW() "
            "WHERE code = %s AND is_used = FALSE RETURNING id",
            (user_id, code),
        )
        row = cur.fetchone()
        if not row:
            return False  # 激活码不存在或已被使用
        code_id = row[0]
        cur.execute(
            "UPDATE users SET is_premium = TRUE, activation_code_id = %s, updated_at = NOW() WHERE id = %s",
            (code_id, user_id),
        )
        return True


def batch_create_codes(count: int) -> list:
    """批量生成激活码（管理员用）。返回激活码列表。"""
    import secrets
    codes = []
    with get_conn() as conn:
        cur = conn.cursor()
        for _ in range(count):
            while True:
                code = f"RESUME-{secrets.token_hex(3).upper()}-{secrets.token_hex(3).upper()}"
                try:
                    cur.execute("INSERT INTO activation_codes (code) VALUES (%s)", (code,))
                    codes.append(code)
                    break
                except psycopg2.IntegrityError:
                    conn.rollback()
                    continue
        conn.commit()
    return codes


def list_unused_codes() -> list:
    """列出未使用的激活码。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT code FROM activation_codes WHERE is_used = FALSE ORDER BY created_at DESC")
        return [r[0] for r in cur.fetchall()]


# ========== 数据库可用性检测 ==========
def is_db_available() -> bool:
    """检测数据库是否可用。"""
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            return True
    except Exception:
        return False


# ========== 反馈工单 ==========
def create_feedback(fingerprint: str, content: str, contact: str = "", image_base64: str = "") -> dict:
    """用户提交反馈，返回工单信息。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO feedbacks (fingerprint, content, contact, status, image_base64) "
            "VALUES (%s, %s, %s, 'pending', %s) RETURNING id, created_at",
            (fingerprint, content, contact, image_base64),
        )
        row = cur.fetchone()
        return {"id": str(row[0]), "created_at": row[1].isoformat()}


def _row_to_feedback(r: tuple, include_image: bool = True) -> dict:
    """把 db row 转成 dict（SELECT 顺序: id, content, contact, status, admin_reply, image_base64, ai_analysis, created_at, replied_at）。"""
    d = {
        "id": str(r[0]),
        "content": r[1],
        "contact": r[2] or "",
        "status": r[3],
        "admin_reply": r[4] or "",
        "image_base64": (r[5] or "") if include_image else "",
        "has_image": bool(r[5]),
        "ai_analysis": r[6] or "",
        "ai_analyzed_at": r[7].isoformat() if r[7] else "",
        "created_at": r[8].isoformat() if r[8] else "",
        "replied_at": r[9].isoformat() if r[9] else "",
    }
    return d


def list_my_feedbacks(fingerprint: str) -> list:
    """查询当前用户的所有工单（含截图、AI 分析、管理员回复）。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, content, contact, status, admin_reply, image_base64, "
            "ai_analysis, ai_analyzed_at, created_at, replied_at "
            "FROM feedbacks WHERE fingerprint = %s ORDER BY created_at DESC",
            (fingerprint,),
        )
        return [_row_to_feedback(r) for r in cur.fetchall()]


def list_all_feedbacks(status: str = None, limit: int = 100) -> list:
    """管理员：列出所有工单。status 可选过滤。"""
    with get_conn() as conn:
        cur = conn.cursor()
        sql = (
            "SELECT id, fingerprint, content, contact, status, admin_reply, image_base64, "
            "ai_analysis, ai_analyzed_at, created_at, replied_at "
            "FROM feedbacks"
        )
        if status:
            sql += " WHERE status = %s"
            cur.execute(sql + " ORDER BY created_at DESC LIMIT %s", (status, limit))
        else:
            cur.execute(sql + " ORDER BY created_at DESC LIMIT %s", (limit,))
        results = []
        for r in cur.fetchall():
            d = _row_to_feedback(r, include_image=True)
            d["fingerprint"] = (r[1] or "")[:12] + "..."  # 脱敏指纹
            results.append(d)
        return results


def save_ai_analysis(feedback_id: str, analysis: str) -> bool:
    """保存 AI 自动分析结果。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE feedbacks SET ai_analysis = %s, ai_analyzed_at = NOW() WHERE id = %s RETURNING id",
            (analysis, feedback_id),
        )
        return cur.fetchone() is not None


def get_daily_report(date_str: str = None) -> dict:
    """
    获取每日统计报告。date_str 格式 YYYY-MM-DD，默认今天。
    返回总数、按状态分组、AI 已分析数、热门问题关键词（前5个 content 前50字摘要）。
    """
    from datetime import date, datetime
    target = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*), "
            "COUNT(*) FILTER (WHERE status='pending'), "
            "COUNT(*) FILTER (WHERE status='replied'), "
            "COUNT(*) FILTER (WHERE ai_analysis != ''), "
            "COUNT(*) FILTER (WHERE image_base64 != '') "
            "FROM feedbacks WHERE DATE(created_at AT TIME ZONE 'Asia/Shanghai') = %s",
            (target,),
        )
        row = cur.fetchone()
        total, pending, replied, analyzed, with_image = (row[0], row[1], row[2], row[3], row[4])

        # 今日工单列表（摘要）
        cur.execute(
            "SELECT id, content, status, ai_analysis, admin_reply, created_at "
            "FROM feedbacks WHERE DATE(created_at AT TIME ZONE 'Asia/Shanghai') = %s "
            "ORDER BY created_at DESC LIMIT 50",
            (target,),
        )
        items = []
        for r in cur.fetchall():
            items.append({
                "id": str(r[0]),
                "content_preview": (r[1] or "")[:80],
                "status": r[2],
                "has_ai": bool(r[3]),
                "has_reply": bool(r[4]),
                "created_at": r[5].isoformat() if r[5] else "",
            })

        return {
            "date": target.isoformat(),
            "total": total,
            "pending": pending,
            "replied": replied,
            "ai_analyzed": analyzed,
            "with_image": with_image,
            "items": items,
        }


def reply_feedback(feedback_id: str, reply: str) -> bool:
    """管理员回复工单。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE feedbacks SET admin_reply = %s, status = 'replied', replied_at = NOW() "
            "WHERE id = %s RETURNING id",
            (reply, feedback_id),
        )
        return cur.fetchone() is not None


def delete_feedback(feedback_id: str) -> bool:
    """管理员删除工单。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM feedbacks WHERE id = %s RETURNING id", (feedback_id,))
        return cur.fetchone() is not None
