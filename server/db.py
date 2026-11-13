# -*- coding: utf-8 -*-
"""
数据库操作模块（Supabase PostgreSQL + Redis 每日计数）。
- 用户（设备指纹）管理 + 套餐（free/monthly/permanent）
- 每日免费次数（Redis key: free:{user_id}:{YYYY-MM-DD}，自动过期）
- 激活码验证与核销（支持 30 天 / 永久 两种）
- 反馈工单系统
"""
import os
import time
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional

import psycopg2
from psycopg2.pool import SimpleConnectionPool

DSN = os.getenv("DATABASE_URL", "")

_pool: Optional[SimpleConnectionPool] = None
_pool_lock = threading.Lock()

# Redis client（每日免费次数用）
try:
    import redis as _redis
    _REDIS_URL = os.getenv("REDIS_URL", "")
    _redis_client = _redis.from_url(_REDIS_URL, socket_connect_timeout=2, socket_timeout=2) if _REDIS_URL else None
    if _redis_client:
        _redis_client.ping()
except Exception:
    _redis_client = None


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
    """根据设备指纹获取或创建用户，返回用户信息（含 plan + expires_at）。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, fingerprint, free_calls_used, plan, expires_at, activation_code_id "
            "FROM users WHERE fingerprint = %s",
            (fingerprint,),
        )
        row = cur.fetchone()
        if row:
            plan = row[3] or 'free'
            expires_at = row[4]
            return {
                "id": str(row[0]),
                "fingerprint": row[1],
                "free_calls_used": row[2],
                "plan": plan,
                "expires_at": expires_at.isoformat() if expires_at else None,
                "is_premium": plan != 'free' and _is_plan_active(plan, expires_at),
                "activation_code_id": str(row[5]) if row[5] else None,
            }
        # 新建用户
        cur.execute(
            "INSERT INTO users (fingerprint, plan) VALUES (%s, 'free') RETURNING id",
            (fingerprint,),
        )
        uid = str(cur.fetchone()[0])
        return {
            "id": uid,
            "fingerprint": fingerprint,
            "free_calls_used": 0,
            "plan": "free",
            "expires_at": None,
            "is_premium": False,
            "activation_code_id": None,
        }


def _is_plan_active(plan: str, expires_at) -> bool:
    """检查套餐是否在有效期内。permanent 永远有效，monthly 检查过期时间。"""
    if plan == 'permanent':
        return True
    if plan == 'monthly' and expires_at:
        return expires_at > datetime.now(expires_at.tzinfo) if expires_at.tzinfo else expires_at > datetime.now()
    return False


def get_user_entitlement(user_id: str) -> dict:
    """
    获取用户当前权益。
    返回 {plan: str, is_active: bool, has_premium_templates: bool, expires_at: str|None}
    """
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT plan, expires_at FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if not row:
            return {"plan": "free", "is_active": False, "has_premium_templates": False, "expires_at": None}
        plan = row[0] or 'free'
        expires_at = row[1]
        is_active = _is_plan_active(plan, expires_at)
        return {
            "plan": plan,
            "is_active": is_active,
            "has_premium_templates": plan == 'permanent',  # 仅永久档有高级模板
            "expires_at": expires_at.isoformat() if expires_at else None,
        }


def _today_key(user_id: str) -> str:
    """Redis key: 每日计数。"""
    today = datetime.now().strftime("%Y-%m-%d")
    return f"free:{user_id}:{today}"


def get_daily_remaining(user_id: str, daily_limit: int) -> int:
    """获取今日剩余免费次数（Redis 每日自动重置）。"""
    # 先检查用户是不是付费/过期
    ent = get_user_entitlement(user_id)
    if ent["is_active"]:
        return -1  # 付费用户不限

    if _redis_client:
        try:
            used = int(_redis_client.get(_today_key(user_id)) or 0)
            return max(0, daily_limit - used)
        except Exception:
            pass

    # Redis 不可达降级：用数据库累计（尽量不要走到这）
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT free_calls_used FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return max(0, daily_limit - (row[0] if row else 0))


def increment_daily_free_calls(user_id: str) -> int:
    """今日免费次数 +1，返回当前已用次数。Redis 不可达时降级。"""
    if _redis_client:
        try:
            key = _today_key(user_id)
            pipe = _redis_client.pipeline()
            pipe.incr(key)
            pipe.expire(key, 86400)  # 24 小时后自动过期
            results = pipe.execute()
            return results[0] or 0
        except Exception:
            pass

    # 降级到数据库
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET free_calls_used = free_calls_used + 1, updated_at = NOW() "
            "WHERE id = %s RETURNING free_calls_used",
            (user_id,),
        )
        row = cur.fetchone()
        return row[0] if row else 0


# ========== 激活码操作 ==========
def verify_and_use_code(code: str, user_id: str) -> dict:
    """
    验证激活码并核销（绑定到用户）。
    成功返回 {"ok": True, "plan": str, "expires_at": str|None}，失败返回 {"ok": False}。
    """
    code = code.strip().upper()
    with get_conn() as conn:
        cur = conn.cursor()
        # 条件更新：只更新未使用的激活码（天然防并发，兼容 pooler）
        # 同时读取 plan 和 days
        cur.execute(
            "UPDATE activation_codes SET is_used = TRUE, used_by = %s, used_at = NOW() "
            "WHERE code = %s AND is_used = FALSE RETURNING id, plan, days",
            (user_id, code),
        )
        row = cur.fetchone()
        if not row:
            return {"ok": False}

        code_id, plan, days = row[0], (row[1] or 'permanent'), row[2]

        # 计算 expires_at
        expires_at = None
        if plan == 'monthly' and days:
            expires_at = datetime.utcnow() + timedelta(days=days)

        # 更新用户套餐
        if expires_at:
            cur.execute(
                "UPDATE users SET plan = %s, expires_at = %s, activation_code_id = %s, updated_at = NOW() WHERE id = %s",
                (plan, expires_at, code_id, user_id),
            )
            expires_at_str = expires_at.isoformat()
        else:
            cur.execute(
                "UPDATE users SET plan = %s, expires_at = NULL, activation_code_id = %s, updated_at = NOW() WHERE id = %s",
                (plan, code_id, user_id),
            )
            expires_at_str = None

        return {"ok": True, "plan": plan, "expires_at": expires_at_str}


def batch_create_codes(count: int, plan: str = "permanent", days: int = None) -> list:
    """
    批量生成激活码（管理员用）。
    plan: 'permanent'（¥19.9 永久）或 'monthly'（¥9.9 30 天）
    days: monthly 时必填，permanent 时忽略
    """
    import secrets
    codes = []
    with get_conn() as conn:
        cur = conn.cursor()
        for _ in range(count):
            while True:
                code = f"RESUME-{secrets.token_hex(3).upper()}-{secrets.token_hex(3).upper()}"
                try:
                    cur.execute(
                        "INSERT INTO activation_codes (code, plan, days) VALUES (%s, %s, %s)",
                        (code, plan, days),
                    )
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
    """把 db row 转成 dict（SELECT 顺序: id, content, contact, status, admin_reply, image_base64, ai_analysis, ai_fix_plan, created_at, replied_at）。"""
    d = {
        "id": str(r[0]),
        "content": r[1],
        "contact": r[2] or "",
        "status": r[3],
        "admin_reply": r[4] or "",
        "image_base64": (r[5] or "") if include_image else "",
        "has_image": bool(r[5]),
        "ai_analysis": r[6] or "",
        "ai_fix_plan": r[7] or "",
        "ai_analyzed_at": r[8].isoformat() if r[8] else "",
        "created_at": r[9].isoformat() if r[9] else "",
        "replied_at": r[10].isoformat() if r[10] else "",
    }
    return d


def list_my_feedbacks(fingerprint: str) -> list:
    """查询当前用户的所有工单（含截图、AI 分析、管理员回复）。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, content, contact, status, admin_reply, image_base64, "
            "ai_analysis, ai_fix_plan, ai_analyzed_at, created_at, replied_at "
            "FROM feedbacks WHERE fingerprint = %s ORDER BY created_at DESC",
            (fingerprint,),
        )
        # 用户端不返回 ai_fix_plan（这是管理员看的内部信息）
        results = []
        for r in cur.fetchall():
            d = _row_to_feedback(r)
            d.pop("ai_fix_plan", None)
            results.append(d)
        return results


def list_all_feedbacks(status: str = None, limit: int = 100) -> list:
    """管理员：列出所有工单。status 可选过滤。"""
    with get_conn() as conn:
        cur = conn.cursor()
        sql = (
            "SELECT id, fingerprint, content, contact, status, admin_reply, image_base64, "
            "ai_analysis, ai_fix_plan, ai_analyzed_at, created_at, replied_at "
            "FROM feedbacks"
        )
        if status:
            sql += " WHERE status = %s"
            cur.execute(sql + " ORDER BY created_at DESC LIMIT %s", (status, limit))
        else:
            cur.execute(sql + " ORDER BY created_at DESC LIMIT %s", (limit,))
        results = []
        for r in cur.fetchall():
            # r 比 _row_to_feedback 多了 fingerprint（第2列）
            # 把它挪成 d["fingerprint"]，然后切片给 _row_to_feedback
            fp = (r[1] or "")[:12] + "..."
            d = _row_to_feedback(r[:1] + r[2:])  # 去掉 fingerprint 列
            d["fingerprint"] = fp
            results.append(d)
        return results


def save_ai_results(feedback_id: str, analysis: str, fix_plan: str) -> bool:
    """保存 AI 分析 + 修复方案。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE feedbacks SET ai_analysis = %s, ai_fix_plan = %s, ai_analyzed_at = NOW() "
            "WHERE id = %s RETURNING id",
            (analysis, fix_plan, feedback_id),
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
