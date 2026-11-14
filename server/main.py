# -*- coding: utf-8 -*-
"""
简历编辑器后端服务。
- AI 改写代理（智谱 GLM）
- Redis 多层限流
- 统一异常处理
- 隐私政策页
"""
import logging
import os
import hashlib
import secrets
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Request, HTTPException, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .rate_limit import limiter
from .ai_client import (
    call_llm, call_vision_ocr, AIClientError, AITimeoutError, AISafetyError,
    AIRateLimitError, AIUnknownError,
)
from .feishu_client import (
    FeishuError, parse_bitable_url, list_tables, list_records, extract_company_position,
    _get_tenant_access_token,
)
from . import db

# ========== 日志配置：只记元信息，绝不打印简历内容 ==========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("resume-editor")

app = FastAPI(title="简历编辑器后端", version="1.0.0")

# CORS：公开发布时建议限定具体域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== 请求 / 响应模型 ==========
class AIChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    system_prompt: Optional[str] = None
    fingerprint: Optional[str] = None  # 设备指纹，用于配额检查

    class Config:
        # 禁止打印请求体到日志
        json_encoders = {}


class AIChatResponse(BaseModel):
    content: str


class OCRRequest(BaseModel):
    """截图 OCR 请求：前端把图片转 base64 后上传。"""
    image_base64: str = Field(..., min_length=1)
    fingerprint: Optional[str] = None  # 设备指纹，用于配额检查


class FeishuBitableRequest(BaseModel):
    """飞书多维表格读取请求。"""
    bitable_url: str = Field(..., min_length=1)
    table_id: Optional[str] = None
    app_id: Optional[str] = None      # 前端用户自带的飞书应用 App ID（优先于 .env）
    app_secret: Optional[str] = None  # 前端用户自带的飞书应用 App Secret


class FeishuTestRequest(BaseModel):
    """飞书连接测试请求（仅验证凭证是否有效，不需要表格）。"""
    app_id: Optional[str] = None
    app_secret: Optional[str] = None


class IdentifyRequest(BaseModel):
    """设备指纹识别请求。"""
    fingerprint: str = Field(..., min_length=10)


class ActivateRequest(BaseModel):
    """激活码核销请求。"""
    fingerprint: str = Field(..., min_length=10)
    code: str = Field(..., min_length=5)


class FeedbackSubmitRequest(BaseModel):
    """用户提交反馈。"""
    fingerprint: str = Field(..., min_length=10)
    content: str = Field(..., min_length=1, max_length=2000)
    contact: Optional[str] = Field(default="", max_length=200)
    image_base64: Optional[str] = Field(default="", max_length=10 * 1024 * 1024)  # 最大约 10MB


class AdminReplyRequest(BaseModel):
    """管理员回复工单。"""
    reply: str = Field(..., min_length=1, max_length=2000)


class AdminLoginRequest(BaseModel):
    """管理员登录。"""
    password: str


class ResumeParseRequest(BaseModel):
    """AI 解析简历文本为结构化板块。"""
    text: str = Field(..., min_length=10, max_length=20000)
    fingerprint: str = Field(default='', max_length=128)


# 管理员 session 存储（内存，重启失效；简单够用）
_admin_sessions: dict = {}


# ========== 飞书 webhook 通知 ==========
async def notify_feishu_webhook(title: str, content: str):
    """
    向飞书群机器人 webhook 推送消息（异步，失败不阻塞主流程）。
    用户自定义机器人支持 text / post / interactive 消息类型。
    """
    webhook = config.FEISHU_WEBHOOK_URL
    if not webhook:
        return
    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue",
            },
            "elements": [
                {"tag": "div", "text": {"tag": "plain_text", "content": content}},
                {"tag": "note", "elements": [
                    {"tag": "plain_text", "content": "💡 登录管理后台 /admin 查看并回复"}
                ]},
            ],
        },
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(webhook, json=payload)
            if r.status_code != 200:
                logger.warning("飞书 webhook 推送失败: %s %s", r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("飞书 webhook 推送异常: %s", e)


# ========== 工具函数 ==========
def get_client_ip(request: Request) -> str:
    """获取客户端真实 IP（兼容反向代理）。"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit_dependency(request: Request):
    """
    限流依赖注入。
    通过后将剩余次数写入 request.state.remaining。
    被拦截时抛出 HTTPException(429)。
    """
    ip = get_client_ip(request)
    # TODO: 接入登录系统后，从 request 中解析 user_id
    user_id = None

    passed, window, remaining = limiter.check(ip, user_id)
    request.state.remaining = remaining

    if not passed:
        if window == "global":
            msg = "AI 服务今日已达使用上限，明日再来"
        elif window == "ip_minute":
            msg = "操作太频繁了，稍后再试"
        elif window in ("ip_day", "user_day"):
            msg = "今日免费次数已用完，明日再来"
        else:
            msg = "请求过于频繁"
        raise HTTPException(status_code=429, detail=msg)


# ========== 路由 ==========
@app.get("/health", tags=["health"])
def health():
    return {"status": "ok", "ai_configured": bool(config.AI_API_KEY)}


@app.get("/privacy", response_class=HTMLResponse, tags=["privacy"])
def privacy_page():
    """隐私政策静态页。"""
    html_path = Path(__file__).parent / "privacy.html"
    html = html_path.read_text(encoding="utf-8")
    html = html.replace("__EFFECTIVE_DATE__", config.PRIVACY_EFFECTIVE_DATE)
    html = html.replace("__CONTACT_EMAIL__", config.CONTACT_EMAIL)
    return HTMLResponse(content=html)


# ========== 前端静态资源托管（同源部署用）==========
FRONTEND_DIR = Path(__file__).parent.parent

@app.get("/app", response_class=HTMLResponse, tags=["frontend"])
def frontend_app():
    """托管前端简历编辑器页面（同源部署时访问 /app）。"""
    html_path = FRONTEND_DIR / "简历编辑器.html"
    return FileResponse(html_path, media_type="text/html")


@app.get("/", response_class=HTMLResponse, tags=["frontend"])
def frontend_root():
    """根路径直接返回简历编辑器页面。"""
    html_path = FRONTEND_DIR / "简历编辑器.html"
    return FileResponse(html_path, media_type="text/html")


@app.post("/api/ai/chat", tags=["ai"], dependencies=[Depends(rate_limit_dependency)])
def ai_chat(req: AIChatRequest, request: Request):
    """
    AI 改写统一入口。
    - 输入长度校验 ≤ 5000 字符
    - 配额检查（免费次数 / 买断用户）
    - 调用大模型并加 NOT_A_RESUME 护栏
    - 返回剩余次数响应头
    """
    total_len = len(req.prompt) + len(req.system_prompt or "")
    if total_len > config.MAX_INPUT_LENGTH:
        raise HTTPException(
            status_code=413,
            detail=f"输入内容过长（{total_len} 字符），请精简至 {config.MAX_INPUT_LENGTH} 字符以内",
        )

    # 配额检查
    quota = check_quota(req.fingerprint or "")
    if not quota["ok"]:
        raise HTTPException(
            status_code=403,
            detail=f"今日免费次数已用完（{config.FREE_CALLS_DAILY}次/天），请购买 ¥{config.MONTHLY_PRICE} 30天或 ¥{config.PERMANENT_PRICE} 永久版后继续使用",
        )

    # 调用 AI
    try:
        content = call_llm(req.prompt, req.system_prompt)
    except AITimeoutError as e:
        logger.warning("AI 调用超时 ip=%s", get_client_ip(request))
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except AISafetyError as e:
        logger.warning("AI 内容安全拦截 ip=%s", get_client_ip(request))
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except AIRateLimitError as e:
        logger.warning("AI 服务商限流 ip=%s", get_client_ip(request))
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except AIUnknownError as e:
        # 日志记录内部错误详情，但不包含简历内容
        logger.error("AI 调用未知错误 ip=%s detail=%s", get_client_ip(request), e.detail)
        raise HTTPException(status_code=e.status_code, detail=e.message)

    # 护栏：模型判定非简历
    if content.strip() == config.NOT_A_RESUME_FLAG:
        raise HTTPException(status_code=422, detail="请粘贴简历内容后再试")

    # AI 调用成功，递增每日免费次数（仅免费用户）
    if not quota["is_premium"] and quota["user_id"]:
        try:
            db.increment_daily_free_calls(quota["user_id"])
        except Exception as e:
            logger.warning("每日免费次数递增失败: %s", e)

    remaining = quota["remaining"] if quota["is_premium"] else max(0, quota["remaining"] - 1)
    return JSONResponse(
        content={"content": content},
        headers={"X-RateLimit-Remaining": str(remaining)},
    )




@app.post("/api/resume/parse", tags=["resume"])
def parse_resume(req: ResumeParseRequest, request: Request):
    """AI 把简历纯文本解析成结构化板块 JSON。前端上传 PDF/docx 后调用。"""
    import json
    ip = get_client_ip(request)

    system_prompt = """你是简历结构化解析专家。用户会给你一段从 PDF/Word 简历里提取的纯文本，
你需要把它拆成以下板块并严格按 JSON 输出（只输出 JSON，不要任何其他文字）：

JSON schema：
{
  "header": "姓名\n手机\n邮箱\n求职意向...",
  "education": "辽宁大学 外汇学 硕士 2025.09-2026.08\n新疆财经大学 国际经济与贸易 本科 2020.09-2024.06",
  "internship": "北京新闻科技有限公司 产品运营实习生 2026.06-2026.08",
  "projects": "金陵风物志 小红书 vibecoding 大赛参赛作品 2026.06-至今",
  "skills": "数据分析 Excel SQL...",
  "missing_sections": []
}

规则：
1. 每个板块内多条记录用 \n 换行，每条一行
2. 完整保留所有信息，不要删减
3. 忽略板块关键词（教育背景/EDUCATION/实习经历/INTERNSHIP/项目经历/PROJECTS/技能/SKILLS）
4. 没找到的板块设为空字符串
5. 只输出 JSON，不要 markdown fence 或解释"""

    try:
        raw = call_llm(req.text[:15000], system_prompt)
        cleaned = raw.strip()
        # 去掉可能的 ```json fence
        if cleaned.startswith('```'):
            cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
            cleaned = re.sub(r'\s*```$', '', cleaned)
            cleaned = cleaned.strip()
        parsed = json.loads(cleaned)
    except Exception as e:
        logger.warning("简历解析失败 ip=%s: %s", ip, e)
        parsed = {"header": "", "education": "", "internship": "", "projects": "", "skills": "", "missing_sections": ["all"]}

    for k in ["header", "education", "internship", "projects", "skills"]:
        if k not in parsed:
            parsed[k] = ""
    if "missing_sections" not in parsed:
        parsed["missing_sections"] = []

    logger.info("简历解析 ip=%s h=%s e=%s i=%s p=%s s=%s", ip,
        bool(parsed["header"]), bool(parsed["education"]), bool(parsed["internship"]),
        bool(parsed["projects"]), bool(parsed["skills"]))
    return JSONResponse(content={"ok": True, "sections": parsed})

@app.post("/api/ai/ocr", tags=["ai"], dependencies=[Depends(rate_limit_dependency)])
def ai_ocr(req: OCRRequest, request: Request):
    """
    JD 截图 OCR 识别。
    - 接收 base64 图片，调用智谱 GLM-4V 提取文字
    - 图片仅在内存中转，不写盘
    - 受统一限流约束 + 配额检查
    """
    # 限制图片大小：base64 约 5MB（约 3.75MB 原图）
    if len(req.image_base64) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图片过大，请压缩后再上传")

    # 配额检查
    quota = check_quota(req.fingerprint or "")
    if not quota["ok"]:
        raise HTTPException(
            status_code=403,
            detail=f"今日免费次数已用完（{config.FREE_CALLS_DAILY}次/天），请购买 ¥{config.MONTHLY_PRICE} 30天或 ¥{config.PERMANENT_PRICE} 永久版后继续使用",
        )

    try:
        text = call_vision_ocr(req.image_base64)
    except AITimeoutError as e:
        logger.warning("OCR 超时 ip=%s", get_client_ip(request))
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except AISafetyError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except AIRateLimitError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except AIUnknownError as e:
        logger.error("OCR 未知错误 ip=%s detail=%s", get_client_ip(request), e.detail)
        raise HTTPException(status_code=e.status_code, detail=e.message)

    # OCR 成功，递增每日免费次数
    if not quota["is_premium"] and quota["user_id"]:
        try:
            db.increment_daily_free_calls(quota["user_id"])
        except Exception as e:
            logger.warning("每日免费次数递增失败: %s", e)

    remaining = quota["remaining"] if quota["is_premium"] else max(0, quota["remaining"] - 1)
    return JSONResponse(
        content={"content": text},
        headers={"X-RateLimit-Remaining": str(remaining)},
    )


@app.post("/api/feishu/bitable/records", tags=["feishu"])
def feishu_bitable_records(req: FeishuBitableRequest, request: Request):
    """
    从飞书多维表格读取投递记录，提取公司与岗位。
    流程：解析 URL → 获取表列表（如未指定 table_id）→ 读取记录 → 提取公司/岗位
    凭证优先使用请求体中的 app_id/app_secret，否则回退到服务端 .env 配置。
    不存储任何用户数据。
    """
    ip = get_client_ip(request)
    app_token, table_id = parse_bitable_url(req.bitable_url)
    if not app_token:
        raise HTTPException(status_code=400, detail="无法从链接中解析多维表格 ID，请检查链接格式")

    # 飞书接口不计入 AI 限流（它不调用大模型），但做简单频率保护
    passed, _, remaining = limiter.check(ip, None)
    if not passed:
        raise HTTPException(status_code=429, detail="操作太频繁了，稍后再试")

    try:
        # 如果没传 table_id，先列出所有表，取第一个
        if not table_id:
            tables = list_tables(app_token, req.app_id, req.app_secret)
            if not tables:
                raise HTTPException(status_code=404, detail="该多维表格下没有数据表")
            table_id = tables[0].get("table_id")

        records = list_records(app_token, table_id, req.app_id, req.app_secret)
        items = extract_company_position(records)
        logger.info("飞书表格读取成功 ip=%s records=%d matched=%d", ip, len(records), len(items))
        return JSONResponse(content={"records": items, "total": len(records)})
    except FeishuError as e:
        logger.warning("飞书接口错误 ip=%s msg=%s", ip, e.message)
        raise HTTPException(status_code=e.code, detail=e.message)


@app.post("/api/feishu/test", tags=["feishu"])
def feishu_test_connection(req: FeishuTestRequest, request: Request):
    """
    测试飞书应用凭证是否有效（不需要多维表格）。
    成功返回 token 前缀（脱敏），失败返回具体错误。
    """
    ip = get_client_ip(request)
    try:
        token = _get_tenant_access_token(req.app_id, req.app_secret)
        logger.info("飞书连接测试成功 ip=%s", ip)
        # 只返回 token 前 8 位用于确认，不返回完整 token
        return JSONResponse(content={
            "ok": True,
            "token_preview": token[:8] + "..." if token else "",
            "message": "凭证有效，已成功获取 tenant_access_token",
        })
    except FeishuError as e:
        logger.warning("飞书连接测试失败 ip=%s msg=%s", ip, e.message)
        raise HTTPException(status_code=e.code, detail=e.message)


# ========== 商业化：设备指纹 + 每日配额 + 激活码（3档） ==========
def check_quota(fingerprint: str) -> dict:
    """
    检查用户配额。
    返回 {ok: bool, remaining: int, is_premium: bool, plan: str, user_id: str, entitlement: dict}
    """
    try:
        user = db.get_or_create_user(fingerprint)
        ent = db.get_user_entitlement(user["id"])
        is_premium = ent["is_active"]
        if is_premium:
            return {
                "ok": True, "remaining": -1, "is_premium": True,
                "plan": ent["plan"], "user_id": user["id"], "entitlement": ent,
            }
        remaining = db.get_daily_remaining(user["id"], config.FREE_CALLS_DAILY)
        return {
            "ok": remaining > 0, "remaining": remaining, "is_premium": False,
            "plan": "free", "user_id": user["id"], "entitlement": ent,
        }
    except Exception as e:
        logger.warning("数据库不可用，降级放行: %s", e)
        return {"ok": True, "remaining": -1, "is_premium": False, "plan": "free", "user_id": "", "entitlement": {}}


@app.post("/api/auth/identify", tags=["auth"])
def identify_device(req: IdentifyRequest, request: Request):
    """设备指纹识别：返回用户状态（套餐、每日剩余、高级模板权限）。"""
    ip = get_client_ip(request)
    try:
        user = db.get_or_create_user(req.fingerprint)
        ent = db.get_user_entitlement(user["id"])
        if ent["is_active"]:
            remaining = -1
        else:
            remaining = db.get_daily_remaining(user["id"], config.FREE_CALLS_DAILY)
        logger.info("设备识别 ip=%s plan=%s active=%s", ip, ent["plan"], ent["is_active"])
        return JSONResponse(content={
            "plan": ent["plan"],
            "is_premium": ent["is_active"],
            "has_premium_templates": ent["has_premium_templates"],
            "expires_at": ent["expires_at"],
            "remaining": remaining,
            "daily_limit": config.FREE_CALLS_DAILY,
            "monthly_price": config.MONTHLY_PRICE,
            "permanent_price": config.PERMANENT_PRICE,
        })
    except Exception as e:
        logger.warning("设备识别失败（降级放行）: %s", e)
        return JSONResponse(content={
            "plan": "free", "is_premium": False, "has_premium_templates": False,
            "expires_at": None, "remaining": config.FREE_CALLS_DAILY,
            "daily_limit": config.FREE_CALLS_DAILY,
            "monthly_price": config.MONTHLY_PRICE,
            "permanent_price": config.PERMANENT_PRICE,
        })


@app.post("/api/auth/activate", tags=["auth"])
def activate_code(req: ActivateRequest, request: Request):
    """激活码核销：支持 monthly（¥9.9 30天）和 permanent（¥19.9 永久）两种。"""
    ip = get_client_ip(request)
    try:
        user = db.get_or_create_user(req.fingerprint)
        ent = db.get_user_entitlement(user["id"])
        if ent["is_active"]:
            return JSONResponse(content={
                "ok": True, "message": f"您已是{'永久' if ent['plan']=='permanent' else '30天'}用户，无需重复激活",
                "is_premium": True, "plan": ent["plan"], "expires_at": ent["expires_at"],
            })
        result = db.verify_and_use_code(req.code, user["id"])
        if result["ok"]:
            plan_label = "永久" if result["plan"] == "permanent" else f"30天（到期 {result['expires_at'][:10] if result['expires_at'] else ''}）"
            logger.info("激活码核销成功 ip=%s plan=%s", ip, result["plan"])
            return JSONResponse(content={
                "ok": True, "message": f"激活成功！已升级为{plan_label}套餐",
                "is_premium": True, "plan": result["plan"], "expires_at": result["expires_at"],
                "has_premium_templates": result["plan"] == "permanent",
            })
        return JSONResponse(status_code=400, content={"ok": False, "message": "激活码无效或已被使用"})
    except Exception as e:
        import traceback
        logger.error("激活码核销失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail="激活服务异常，请稍后再试")


# ========== AI 客服：反馈工单 ==========

def _run_ai_feedback_analysis(feedback_id: str, content: str, image_base64: str, fingerprint: str = ""):
    """
    AI 自动分析反馈（同步调用）。
    三个结果：
      1. ai_analysis — 给用户/管理员看的诊断
      2. ai_fix_plan — 给管理员看的修复方案
      3. 自动发激活码 — 如果识别到付款截图（¥9.9/¥19.9），直接发码
    """
    import threading
    try:
        # 抽掉 data:image/xxx;base64, 前缀
        img_clean = ""
        ocr_text = ""
        if image_base64:
            prefix = image_base64.find(",")
            img_clean = image_base64[prefix+1:] if prefix >= 0 else image_base64
            try:
                ocr_text = call_vision_ocr(img_clean)
            except Exception:
                ocr_text = "（截图识别失败）"

        # ===== 新增：付款截图识别 + 自动发激活码 =====
        auto_code_result = _try_auto_issue_code(feedback_id, content, ocr_text, fingerprint)

        # ===== 第一步：AI 诊断 =====
        context_text = content + (f"\n\n[截图识别内容]\n{ocr_text}" if ocr_text else "")
        analysis_prompt = f"""请分析以下用户反馈，判断问题类型、严重程度，并给出可能原因和解决建议。

用户反馈：
{context_text}

请严格按以下 JSON 格式输出，只输出 JSON：
{{
  "category": "问题分类（功能Bug/UI问题/性能/使用困惑/功能请求/付款购买/其他）",
  "severity": "严重程度（high/medium/low）",
  "possible_cause": "可能原因分析，50字以内",
  "suggestion": "给用户的解决建议，100字以内",
  "need_human": "是否需要管理员介入（true/false）"
}}"""

        analysis_raw = call_llm(analysis_prompt, system_prompt="你是简历编辑器的技术客服，负责分析用户反馈并给出诊断。严格按 JSON 输出。")
        analysis_text = _format_ai_analysis(analysis_raw)
        analysis_json = _safe_parse_json(analysis_raw)
        category = analysis_json.get("category", "") if analysis_json else ""

        # ===== 第二步：AI 生成修复方案（仅 bug/UI/性能问题才做）=====
        fix_plan_text = ""
        if category in ("功能Bug", "UI问题", "性能"):
            fix_plan_text = _generate_fix_plan(context_text, analysis_json or {})

        # 如果 AI 已经自动发了码，把发码信息拼进 analysis 里让用户能看到
        if auto_code_result and auto_code_result.get("ok"):
            plan_label = "永久版" if auto_code_result["plan"] == "permanent" else f"30天版（到期 {auto_code_result['expires_at'][:10] if auto_code_result.get('expires_at') else ''}）"
            auto_section = f"\n\n{'='*20}\n🎉 <b>AI 客服已自动识别付款截图并发放激活码！</b>\n\n<b>你的激活码：{auto_code_result['code']}</b>\n\n套餐：{plan_label}\n\n直接复制激活码，去简历编辑器顶栏点「⚡ 升级」按钮粘贴即可激活。\n{'='*20}\n"
            analysis_text = auto_section + analysis_text
            # 同时把激活码直接写进 admin_reply，让用户刷新时角标能看到
            reply_text = f"🎉 AI 客服自动识别付款成功，已发放激活码：\n\n<b>{auto_code_result['code']}</b>\n\n点击顶栏「⚡ 升级」→ 输入激活码即可。"
            # 用 fingerprint 找 feedbacks表里的 user_id
            try:
                with db.get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "UPDATE feedbacks SET admin_reply = %s, status = 'replied', replied_at = NOW() WHERE id = %s",
                        (reply_text, feedback_id),
                    )
                    conn.commit()
            except Exception:
                pass

        # 存数据库
        db.save_ai_results(feedback_id, analysis_text, fix_plan_text)
        logger.info("AI 反馈分析完成 fid=%s category=%s has_fix=%s auto_code=%s", feedback_id, category, bool(fix_plan_text), bool(auto_code_result and auto_code_result.get("ok")))
    except Exception as e:
        import traceback
        logger.warning("AI 反馈分析失败 fid=%s: %s\n%s", feedback_id, e, traceback.format_exc())


def _try_auto_issue_code(feedback_id: str, content: str, ocr_text: str, fingerprint: str) -> dict | None:
    """
    尝试自动识别付款截图并发激活码。
    返回 {"ok": True, "code": str, "plan": str, "expires_at": str|None} 或 None。
    """
    if not ocr_text and not content:
        return None  # 没内容没截图，跳过

    # 拼上下文让 AI 判断
    text = (content or "") + "\n" + (ocr_text or "")
    # 快速关键词初筛（省 API 调用，OCR 没识别出来就不走）
    price_found = None
    if "19.9" in text or "19.90" in text:
        price_found = "permanent"
    elif "9.9" in text or "9.90" in text:
        price_found = "monthly"

    # 也可能用户文字说明，比如"我买了19.9的永久"
    if not price_found:
        if any(k in text for k in ["永久", "permanent", "高级模板"]):
            price_found = "permanent"
        elif any(k in text for k in ["30天", "月度", "monthly", "9.9"]):
            price_found = "monthly"

    if not price_found:
        return None

    # 进一步确认是不是付款截图场景（防误判）
    # 给轻量 prompt 验证
    try:
        verify_prompt = f"""判断以下内容是否为用户付款成功的截图/文字，准备购买简历编辑器的激活码。
用户说：{content[:200]}
截图识别内容：{ocr_text[:500]}

输出 JSON：{{"is_payment": true/false, "amount": "9.9"|"19.9"|"unknown"}}"""
        verify_raw = call_llm(verify_prompt, system_prompt="你是支付验证助手，严格按 JSON 输出。")
        verify_json = _safe_parse_json(verify_raw)
        if not verify_json or not verify_json.get("is_payment"):
            return None
        amount = verify_json.get("amount", "unknown")
        if amount == "9.9":
            price_found = "monthly"
        elif amount == "19.9":
            price_found = "permanent"
        else:
            return None
    except Exception as e:
        logger.warning("付款验证 AI 调用失败，跳过自动发码 fid=%s: %s", feedback_id, e)
        return None

    # 验证通过 → 取码！
    # 先找 feedback 关联的 user_id
    user_id = None
    if fingerprint:
        try:
            with db.get_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id FROM users WHERE fingerprint = %s", (fingerprint,))
                r = cur.fetchone()
                if r:
                    user_id = str(r[0])
        except Exception:
            pass

    if not user_id:
        # 没找到用户也没关系，用 feedback_id 临时占位
        user_id = f"feedback-{feedback_id}"

    result = db.claim_unused_code(price_found, user_id, fingerprint)
    if not result.get("ok"):
        logger.warning("AI 自动发码失败 fid=%s: %s", feedback_id, result.get("reason", "unknown"))
        return None

    logger.info("🎉 AI 客服自动发码成功 fid=%s plan=%s code=%s", feedback_id, price_found, result["code"])
    return result


def _generate_fix_plan(context_text: str, analysis: dict) -> str:
    """给管理员看的修复方案——基于项目知识地图，告诉 AI 改哪个文件、改什么。"""
    project_map = """【项目知识地图】这是一个简历编辑器 Web 应用，Render 部署（FastAPI 后端 + 静态前端）：

前端（单文件）：
  - 简历编辑器.html          主页面，HTML+CSS+JS 全在里面，contenteditable 编辑简历
    关键函数区域：
    * generateFingerprint()   Canvas+WebGL 生成设备指纹
    * callLLM()               调 AI 改写（走后端 /api/ai/chat）
    * runJDAnalyze()          JD 匹配分析
    * exportPDF()              导出 PDF
    * openUploadModal()       上传简历（.docx/.pdf）
    * showPremiumModal()      买断弹窗
    * identifyDevice()        页面加载时识别设备 + 查配额
    * clearAllData()           清 localStorage

后端（Python FastAPI，server/ 目录下）：
  - server/main.py            所有 API 路由
    * POST /api/auth/identify  设备识别，返回配额
    * POST /api/auth/activate  激活码核销
    * POST /api/ai/chat        AI 改写（配额检查 + 限流）
    * POST /api/ai/ocr         JD 截图 OCR
    * POST /api/feedback/*     反馈工单
    * GET  /api/admin/*        管理员接口
  - server/db.py              Supabase PostgreSQL 连接 + 表操作
    * users 表                 fingerprint, free_calls_used, is_premium
    * activation_codes 表      code, is_used, used_by
    * feedbacks 表             工单
  - server/ai_client.py        智谱 GLM 调用（call_llm, call_vision_ocr）
  - server/feishu_client.py   飞书多维表格读取
  - server/rate_limit.py      Redis 多层限流
  - server/config.py           环境变量（AI_API_KEY, FREE_CALLS_LIMIT=5 等）"""

    prompt = f"""基于下面的项目知识地图和用户反馈/AI 诊断，
请给出一个**可执行的修复方案**给开发者（管理员）看。

输出格式（严格按这个，不用 JSON）：
1️⃣ 【改哪个文件】文件名 + 函数名
2️⃣ 【改什么】问题根因 + 修改思路（1-3 句话）
3️⃣ 【代码改动示意】给出关键代码片段（5-20 行），标注删除/新增
4️⃣ 【验证方法】改完后怎么确认修好了
5️⃣ 【风险评估】会不会影响其他功能（如果有，标出来）

用户反馈：
{context_text[:800]}

AI 诊断：
  分类：{analysis.get('category','-')}
  严重度：{analysis.get('severity','-')}
  可能原因：{analysis.get('possible_cause','-')}

项目知识地图：
{project_map}

如果问题太模糊或信息不够，输出："⚠️ 信息不足，需要更多上下文才能给出修复方案"。"""

    try:
        return call_llm(prompt, system_prompt="你是资深全栈开发者，负责给 bug 写修复方案。")[:800]
    except Exception as e:
        return f"⚠️ AI 生成修复方案失败：{e}"


def _safe_parse_json(raw: str) -> dict | None:
    """容错 JSON 解析，失败返回 None。"""
    import re, json
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end+1]
    try:
        return json.loads(text)
    except Exception:
        return None


def _format_ai_analysis(raw: str) -> str:
    """把 AI 返回的 JSON 格式化成可读文本（容错 markdown 包裹 + JSON 解析失败降级）。"""
    obj = _safe_parse_json(raw)
    if not obj:
        return raw[:500]
    cat = obj.get("category", "未分类")
    sev = obj.get("severity", "unknown")
    sev_map = {"high": "🔴 高", "medium": "🟡 中", "low": "🟢 低", "unknown": "⚪ 未知"}
    cause = obj.get("possible_cause", "")
    suggestion = obj.get("suggestion", "")
    need = obj.get("need_human", False)
    lines = [f"【分类】{cat}  严重度 {sev_map.get(sev, sev)}"]
    if cause:
        lines.append(f"【可能原因】{cause}")
    if suggestion:
        lines.append(f"【AI 建议】{suggestion}")
    lines.append(f"【需人工介入】{'是' if need else '否'}")
    return "\n".join(lines)


@app.post("/api/feedback/submit", tags=["feedback"])
async def submit_feedback(req: FeedbackSubmitRequest, request: Request):
    """用户提交反馈工单（支持可选截图 + AI 自动分析）。"""
    import asyncio
    ip = get_client_ip(request)
    try:
        # 图片大小检查
        if req.image_base64 and len(req.image_base64) > 5 * 1024 * 1024:
            return JSONResponse(status_code=413, content={"ok": False, "detail": "图片过大，请压缩后再上传（建议 1MB 以内）"})

        fb = db.create_feedback(
            req.fingerprint,
            req.content.strip(),
            (req.contact or "").strip(),
            image_base64=req.image_base64 or "",
        )
        logger.info("反馈已提交 ip=%s fid=%s has_image=%s", ip, fb["id"], bool(req.image_base64))

        # 后台线程触发 AI 分析（fire-and-forget，Render Free 版休眠也没关系——下次有人查工单时惰性触发）
        import threading
        threading.Thread(
            target=_run_ai_feedback_analysis,
            args=(fb["id"], req.content, req.image_base64 or "", req.fingerprint),
            daemon=True,
        ).start()

        # 飞书通知（附带 AI 分析状态）
        try:
            img_hint = "📷 含截图" if req.image_base64 else "（无截图）"
            await notify_feishu_webhook(
                title="📮 新用户反馈",
                content=f"用户 IP: {ip}\n联系方式: {req.contact or '（未留）'}\n{img_hint}\n\n{req.content[:500]}\n\n🤖 AI 正在自动分析...",
            )
        except Exception:
            pass

        return JSONResponse(content={
            "ok": True,
            "feedback": fb,
            "ai_analyzing": True,
            "message": "提交成功！AI 正在自动分析问题，稍后你可以在「我的工单」里查看",
        })
    except Exception as e:
        logger.error("反馈提交失败: %s", e)
        raise HTTPException(status_code=500, detail="反馈提交失败，请稍后再试")


@app.post("/api/feedback/mine", tags=["feedback"])
def my_feedbacks(req: IdentifyRequest, request: Request):
    """查询当前用户的所有工单（含管理员回复）。会触发 AI 惰性分析。"""
    try:
        items = db.list_my_feedbacks(req.fingerprint)
        # 惰性触发：如果有工单还没 AI 分析，同步跑一下（最多 1 个，避免阻塞）
        _lazy_analyze_pending(items[:1])
        items = db.list_my_feedbacks(req.fingerprint)  # 重新查
        return JSONResponse(content={"items": items})
    except Exception as e:
        logger.error("查询反馈失败: %s", e)
        raise HTTPException(status_code=500, detail="查询失败")


@app.post("/api/feedback/replied", tags=["feedback"])
def my_replied_feedbacks(req: IdentifyRequest):
    """轻量端点：只返回当前用户已有管理员回复的工单（用于前端轮询角标）。"""
    try:
        items = db.list_my_feedbacks(req.fingerprint)
        replied = [
            {
                "id": t["id"],
                "content": t["content"][:100],
                "admin_reply": t.get("admin_reply", "")[:200],
                "replied_at": t.get("replied_at", ""),
            }
            for t in items
            if t.get("status") == "replied" and t.get("admin_reply")
        ]
        return JSONResponse(content={"items": replied})
    except Exception as e:
        logger.error("查询已回复工单失败: %s", e)
        raise HTTPException(status_code=500, detail="查询失败")


def _lazy_analyze_pending(items: list):
    """同步触发 AI 分析（给还没分析的 pending 工单跑一下，最多 1 个）。"""
    for t in items:
        if not t.get("ai_analysis") and t.get("status") == "pending":
            raw = _get_feedback_raw(t["id"])
            if raw:
                try:
                    _run_ai_feedback_analysis(t["id"], raw["content"], raw["image_base64"] or "")
                except Exception as e:
                    logger.warning("惰性 AI 分析失败 fid=%s: %s", t["id"], e)
            return


def _get_feedback_raw(fid: str) -> dict | None:
    """拿到工单原始数据（content, contact, image_base64, ai_analysis）。"""
    import psycopg2
    from . import db as db_mod
    try:
        with db_mod.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT content, contact, image_base64, ai_analysis FROM feedbacks WHERE id = %s",
                (fid,)
            )
            row = cur.fetchone()
            if row:
                return {
                    "content": row[0] or "",
                    "contact": row[1] or "",
                    "image_base64": row[2] or "",
                    "ai_analysis": row[3] or "",
                }
    except Exception:
        pass
    return None


# ========== 管理员 API ==========
def _require_admin(request: Request):
    """简单管理员鉴权：cookie 里的 session_id 必须在内存里。"""
    sid = request.cookies.get("admin_session", "")
    if sid not in _admin_sessions:
        raise HTTPException(status_code=401, detail="未登录")
    return True


@app.post("/api/admin/login", tags=["admin"])
def admin_login(req: AdminLoginRequest, request: Request):
    """管理员登录（成功返回 set-cookie）。"""
    pwd_hash = hashlib.sha256(req.password.encode()).hexdigest()
    expected = hashlib.sha256(config.ADMIN_PASSWORD.encode()).hexdigest()
    if pwd_hash != expected:
        return JSONResponse(status_code=401, content={"ok": False, "detail": "密码错误"})
    sid = secrets.token_urlsafe(32)
    _admin_sessions[sid] = True
    resp = JSONResponse(content={"ok": True})
    resp.set_cookie("admin_session", sid, httponly=True, max_age=86400, samesite="lax")
    return resp


@app.post("/api/admin/logout", tags=["admin"])
def admin_logout(request: Request):
    sid = request.cookies.get("admin_session", "")
    _admin_sessions.pop(sid, None)
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie("admin_session")
    return resp


@app.get("/api/admin/feedbacks", tags=["admin"])
def admin_list_feedbacks(status: Optional[str] = None, limit: int = 100,
                         _: bool = Depends(_require_admin)):
    """管理员列出所有工单。"""
    items = db.list_all_feedbacks(status=status, limit=min(limit, 500))
    return JSONResponse(content={"items": items})


@app.get("/api/admin/daily-report", tags=["admin"])
def admin_daily_report(date: Optional[str] = None,
                       _: bool = Depends(_require_admin)):
    """
    管理员每日报告。date 格式 YYYY-MM-DD，默认今天。
    返回统计 + 今日工单摘要列表。
    """
    try:
        report = db.get_daily_report(date)
        return JSONResponse(content=report)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/admin/feedbacks/{feedback_id}/reply", tags=["admin"])
def admin_reply(feedback_id: str, req: AdminReplyRequest,
                _: bool = Depends(_require_admin)):
    """管理员回复工单。回复后自动发邮件给用户（如果用户留了邮箱）。"""
    # 先拿到工单原始数据（content, contact, ai_analysis）用于发邮件
    raw = _get_feedback_raw(feedback_id)
    if not raw:
        raise HTTPException(status_code=404, detail="工单不存在")

    ok = db.reply_feedback(feedback_id, req.reply.strip())
    if not ok:
        raise HTTPException(status_code=404, detail="工单不存在")

    # 尝试发邮件通知用户（后台线程，不阻塞回复接口）
    contact = raw.get("contact") or ""
    if contact and "@" in contact:
        import threading
        threading.Thread(
            target=_send_reply_email,
            args=(contact, raw.get("content", ""), req.reply.strip(), raw.get("ai_analysis") or ""),
            daemon=True,
        ).start()
        email_started = True
    else:
        logger.info("用户未留有效邮箱，跳过邮件通知 fid=%s", feedback_id)
        email_started = False

    return JSONResponse(content={"ok": True, "email_sent": email_started})


def _send_reply_email(to_email: str, user_content: str, admin_reply: str, ai_analysis: str):
    """后台线程：发邮件通知用户。任何异常都吞掉，不影响主流程。"""
    try:
        from server.email_client import notify_feedback_reply
        ok = notify_feedback_reply(
            to_email=to_email,
            user_content=user_content,
            admin_reply=admin_reply,
            ai_analysis=ai_analysis,
        )
        logger.info("邮件发送结果 to=%s ok=%s", to_email, ok)
    except Exception as e:
        logger.warning("后台邮件发送失败 to=%s: %s", to_email, e)


@app.delete("/api/admin/feedbacks/{feedback_id}", tags=["admin"])
def admin_delete(feedback_id: str, _: bool = Depends(_require_admin)):
    """管理员删除工单。"""
    ok = db.delete_feedback(feedback_id)
    if not ok:
        raise HTTPException(status_code=404, detail="工单不存在")
    return JSONResponse(content={"ok": True})


@app.get("/api/admin/test-smtp", tags=["admin"])
def admin_test_smtp(_: bool = Depends(_require_admin)):
    """调试用：同步测试邮件连通性。优先 SendGrid > Resend。"""
    from . import config
    import httpx

    result = {
        "sendgrid_configured": bool(config.SENDGRID_API_KEY),
        "resend_configured": bool(config.RESEND_API_KEY),
        "email_user": config.EMAIL_USER,
        "email_user_masked": (config.EMAIL_USER[:3] + "***" + config.EMAIL_USER[-8:]) if config.EMAIL_USER else "",
    }

    if not config.SENDGRID_API_KEY and not config.RESEND_API_KEY:
        result["error"] = "SENDGRID_API_KEY 和 RESEND_API_KEY 都未配置"
        return JSONResponse(content=result)

    to = "XGTX20020526@163.com"  # 固定发到管理员邮箱

    # 优先 SendGrid
    if config.SENDGRID_API_KEY:
        payload = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": config.EMAIL_USER, "name": config.EMAIL_FROM_NAME},
            "subject": "简历编辑器 SendGrid 测试",
            "content": [
                {"type": "text/plain", "value": "✅ SendGrid OK!"},
                {"type": "text/html", "value": "<h2>✅ SendGrid OK!</h2><p>这是同步测试邮件。</p>"},
            ],
        }
        try:
            resp = httpx.post(
                "https://api.sendgrid.com/v3/mail/send",
                json=payload,
                headers={"Authorization": f"Bearer {config.SENDGRID_API_KEY}"},
                timeout=20,
            )
            result["provider"] = "sendgrid"
            result["sendgrid_http_status"] = resp.status_code
            result["sendgrid_response"] = resp.text[:500]
            result["success"] = resp.status_code in (200, 202)
        except Exception as e:
            result["error"] = str(e)
            result["success"] = False
    else:
        # Resend 降级
        payload = {
            "from": f"{config.EMAIL_FROM_NAME} <{config.EMAIL_USER}>",
            "to": [to],
            "subject": "简历编辑器 Resend 测试",
            "html": "<h2>✅ Resend OK!</h2><p>这是同步测试邮件。</p>",
        }
        try:
            resp = httpx.post(
                "https://api.resend.com/emails",
                json=payload,
                headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"},
                timeout=20,
            )
            result["provider"] = "resend"
            result["resend_http_status"] = resp.status_code
            result["resend_response"] = resp.text[:500]
            result["success"] = resp.status_code in (200, 201)
        except Exception as e:
            result["error"] = str(e)
            result["success"] = False

    return JSONResponse(content=result)


@app.get("/api/admin/send-test-email", tags=["admin"])
def admin_send_test_email(to: str = "XGTX20020526@163.com", _: bool = Depends(_require_admin)):
    """调试用：同步发一封邮件到指定收件人。优先 SendGrid。"""
    from . import config
    import httpx

    result = {
        "from": f"{config.EMAIL_FROM_NAME} <{config.EMAIL_USER}>",
        "to": to,
    }

    if not config.SENDGRID_API_KEY and not config.RESEND_API_KEY:
        result["error"] = "SENDGRID_API_KEY 和 RESEND_API_KEY 都未配置"
        return JSONResponse(status_code=400, content=result)

    # 优先 SendGrid
    if config.SENDGRID_API_KEY:
        payload = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": config.EMAIL_USER, "name": config.EMAIL_FROM_NAME},
            "subject": "简历编辑器 SendGrid 同步测试",
            "content": [
                {"type": "text/plain", "value": "✅ 同步测试邮件，收到说明 SendGrid + Render 通了！"},
                {"type": "text/html", "value": "<h2>✅ SendGrid 同步测试</h2><p>如果你收到了，说明 SendGrid + Render 全链路通了！</p>"},
            ],
        }
        try:
            resp = httpx.post(
                "https://api.sendgrid.com/v3/mail/send",
                json=payload,
                headers={"Authorization": f"Bearer {config.SENDGRID_API_KEY}"},
                timeout=20,
            )
            result["provider"] = "sendgrid"
            result["http_status"] = resp.status_code
            result["full_response"] = resp.text[:1000]
            result["success"] = resp.status_code in (200, 202)
        except Exception as e:
            import traceback
            result["error"] = str(e)
            result["traceback"] = traceback.format_exc()[-500:]
            result["success"] = False
    else:
        # Resend 降级
        payload = {
            "from": f"{config.EMAIL_FROM_NAME} <{config.EMAIL_USER}>",
            "to": [to],
            "subject": "简历编辑器 Resend 同步测试",
            "html": "<h2>✅ Resend 同步测试</h2><p>如果你收到了，说明 Resend + Render 全链路通了！</p>",
            "text": "✅ 同步测试邮件，收到说明 Resend + Render 通了！",
        }
        try:
            resp = httpx.post(
                "https://api.resend.com/emails",
                json=payload,
                headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"},
                timeout=20,
            )
            result["provider"] = "resend"
            result["http_status"] = resp.status_code
            result["full_response"] = resp.text[:1000]
            result["success"] = resp.status_code in (200, 201)
        except Exception as e:
            import traceback
            result["error"] = str(e)
            result["traceback"] = traceback.format_exc()[-500:]
            result["success"] = False

    return JSONResponse(content=result)


@app.get("/admin", response_class=HTMLResponse, tags=["admin"])
def admin_page():
    """管理后台页面。"""
    html_path = FRONTEND_DIR / "admin.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return HTMLResponse(content="<h1>admin.html not found</h1>", status_code=404)


# ========== 统一异常处理 ==========
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """统一 HTTP 异常响应，附带剩余次数头。"""
    remaining = getattr(request.state, "remaining", 0)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={"X-RateLimit-Remaining": str(remaining)},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """兜底异常处理，绝不返回堆栈给前端。"""
    logger.exception("未处理异常")
    return JSONResponse(
        status_code=500,
        content={"detail": "AI服务开小差了，请稍后再试"},
        headers={"X-RateLimit-Remaining": "0"},
    )


if __name__ == "__main__":
    import uvicorn
    # 云平台通过 PORT 环境变量指定端口，本地默认 8000
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)


# ========== 静态资源挂载（必须放在所有路由之后，避免覆盖显式路由）==========
class SafeStaticFiles(StaticFiles):
    """只暴露前端资源，禁止访问后端源码、配置、依赖目录。"""

    BLOCKED_PREFIXES = (
        "server", ".env", ".env.example", ".git", ".screenshots",
        "node_modules", "package.json", "package-lock.json", "高海洋-简历.html",
    )

    async def get_response(self, path, scope):
        normalized = path.replace("\\", "/").lstrip("/")
        blocked = any(
            normalized == p or normalized.startswith(p + "/") or normalized.startswith(p)
            for p in self.BLOCKED_PREFIXES
        )
        if blocked:
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)


app.mount("/", SafeStaticFiles(directory=str(FRONTEND_DIR)), name="frontend")
