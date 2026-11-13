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
from pathlib import Path
from typing import Optional

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
            detail=f"今日免费次数已用完（{config.FREE_CALLS_LIMIT}次），请购买买断后继续使用",
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

    # AI 调用成功，递增免费次数（仅非买断用户）
    if not quota["is_premium"] and quota["user_id"]:
        try:
            db.increment_free_calls(quota["user_id"])
        except Exception as e:
            logger.warning("免费次数递增失败: %s", e)

    remaining = quota["remaining"] if quota["is_premium"] else max(0, quota["remaining"] - 1)
    return JSONResponse(
        content={"content": content},
        headers={"X-RateLimit-Remaining": str(remaining)},
    )


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
            detail=f"今日免费次数已用完（{config.FREE_CALLS_LIMIT}次），请购买买断后继续使用",
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

    # OCR 成功，递增免费次数
    if not quota["is_premium"] and quota["user_id"]:
        try:
            db.increment_free_calls(quota["user_id"])
        except Exception as e:
            logger.warning("免费次数递增失败: %s", e)

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


# ========== 商业化：设备指纹 + 免费次数 + 激活码 ==========
def check_quota(fingerprint: str) -> dict:
    """
    检查用户配额。
    返回 {ok: bool, remaining: int, is_premium: bool, user_id: str}
    remaining: 剩余免费次数（-1 表示无限，is_premium=true）
    """
    try:
        user = db.get_or_create_user(fingerprint)
        is_premium = user["is_premium"]
        if is_premium:
            return {"ok": True, "remaining": -1, "is_premium": True, "user_id": user["id"]}
        remaining = db.get_free_calls_remaining(user["id"], config.FREE_CALLS_LIMIT)
        return {"ok": remaining > 0, "remaining": remaining, "is_premium": False, "user_id": user["id"]}
    except Exception as e:
        # 数据库不可用时，降级为放行（不影响现有用户）
        logger.warning("数据库不可用，降级放行: %s", e)
        return {"ok": True, "remaining": -1, "is_premium": False, "user_id": ""}


@app.post("/api/auth/identify", tags=["auth"])
def identify_device(req: IdentifyRequest, request: Request):
    """
    设备指纹识别：返回用户状态（免费次数、是否买断）。
    前端每次加载页面时调用。
    """
    ip = get_client_ip(request)
    try:
        user = db.get_or_create_user(req.fingerprint)
        is_premium = user["is_premium"]
        remaining = -1 if is_premium else db.get_free_calls_remaining(user["id"], config.FREE_CALLS_LIMIT)
        logger.info("设备识别 ip=%s premium=%s", ip, is_premium)
        return JSONResponse(content={
            "is_premium": is_premium,
            "remaining": remaining,
            "free_limit": config.FREE_CALLS_LIMIT,
        })
    except Exception as e:
        logger.warning("设备识别失败（降级放行）: %s", e)
        return JSONResponse(content={
            "is_premium": False,
            "remaining": config.FREE_CALLS_LIMIT,
            "free_limit": config.FREE_CALLS_LIMIT,
        })


@app.post("/api/auth/activate", tags=["auth"])
def activate_code(req: ActivateRequest, request: Request):
    """
    激活码核销：验证激活码并升级为买断用户。
    """
    ip = get_client_ip(request)
    try:
        user = db.get_or_create_user(req.fingerprint)
        if user["is_premium"]:
            return JSONResponse(content={"ok": True, "message": "您已是买断用户，无需重复激活", "is_premium": True})
        success = db.verify_and_use_code(req.code, user["id"])
        if success:
            logger.info("激活码核销成功 ip=%s", ip)
            return JSONResponse(content={"ok": True, "message": "激活成功，已升级为买断用户", "is_premium": True})
        return JSONResponse(status_code=400, content={"ok": False, "message": "激活码无效或已被使用"})
    except Exception as e:
        logger.warning("激活码核销失败: %s", e)
        raise HTTPException(status_code=500, detail="激活服务异常，请稍后再试")


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
