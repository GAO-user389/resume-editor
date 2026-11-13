# -*- coding: utf-8 -*-
"""
配置常量 —— 所有限流阈值、模型参数集中在此，方便后续调整。
生产环境敏感配置通过环境变量注入，不硬编码。
本地开发优先读取同目录下的 .env 文件。
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # 未安装 python-dotenv 时跳过，仍可通过系统环境变量配置

# ========== 限流阈值（可按需调整）==========
# 未登录用户（IP 维度）
RATE_IP_PER_MINUTE = 2          # 每 IP 每分钟最多 AI 调用次数
RATE_IP_PER_DAY = 10            # 每 IP 每日最多 AI 调用次数
# 登录用户维度（当前未做登录系统，预留接口）
RATE_USER_PER_DAY = 5           # 每用户每日最多 AI 调用次数
# 全局熔断
RATE_GLOBAL_PER_DAY = 1000      # 全站 AI 接口每日总调用上限

# ========== 输入防护 ==========
MAX_INPUT_LENGTH = 5000         # 单次输入最大字符数（JD + 简历 + prompt 合并后）
MAX_TOKENS = 2000               # 调用大模型时的 max_tokens
AI_TIMEOUT = 30                 # AI 接口超时时间（秒）

# ========== AI 模型配置 ==========
# 默认使用智谱 GLM-4-Flash（免费），生产可切 GLM-4-Plus
AI_PROVIDER = os.getenv("AI_PROVIDER", "zhipu")
AI_MODEL = os.getenv("AI_MODEL", "glm-4-flash")
AI_API_KEY = os.getenv("AI_API_KEY", "")          # 智谱 API Key，从环境变量读
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")

# ========== Redis 配置 ==========
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ========== 隐私联系信息（发布前替换为真实邮箱）==========
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "XGTX20020526@163.com")
PRIVACY_EFFECTIVE_DATE = os.getenv("PRIVACY_EFFECTIVE_DATE", "2026-11-12")

# ========== 飞书多维表格配置（用于从用户秋招表格导入公司/岗位）==========
# 需在 https://open.feishu.cn 创建自建应用并开通 bitable:app:readonly 权限
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
FEISHU_BASE_URL = os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn")
# OCR 使用的视觉模型（智谱 GLM-4V 系列）
AI_VISION_MODEL = os.getenv("AI_VISION_MODEL", "glm-4v")

# ========== NOT_A_RESUME 护栏标识 ==========
NOT_A_RESUME_FLAG = "NOT_A_RESUME"

# ========== 商业化配置 ==========
FREE_CALLS_DAILY = int(os.getenv("FREE_CALLS_DAILY", "3"))   # 免费：每天 AI 改写次数
MONTHLY_PRICE = os.getenv("MONTHLY_PRICE", "9.9")            # ¥9.9 买断 30 天
PERMANENT_PRICE = os.getenv("PERMANENT_PRICE", "19.9")       # ¥19.9 永久 + 高级模板
MONTHLY_DAYS = int(os.getenv("MONTHLY_DAYS", "30"))          # 30 天期限

# ========== AI 客服 / 工单系统 ==========
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "")    # 飞书群机器人 webhook（用户提交反馈时推送）
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")    # 管理后台密码（务必改！）

# ========== 邮件通知（管理员回复工单时自动发邮件给用户）==========
# Resend HTTP API（推荐，Render Free 版可用）—— 免费每天 100 封
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")           # 从 https://resend.com 注册获取
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")       # 从 https://sendgrid.com 注册获取（免费版无需域名验证）

# 以下是 SMTP 备用（Render Free 版出网被屏蔽，基本不可用）
EMAIL_SMTP_HOST = os.getenv("EMAIL_SMTP_HOST", "smtp.qq.com")
EMAIL_SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "465"))
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "true").lower() == "true"
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "简历编辑器客服")
