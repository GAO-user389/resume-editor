# -*- coding: utf-8 -*-
"""
飞书多维表格客户端。
- 用 App ID + App Secret 换取 tenant_access_token
- 读取多维表格记录，供前端选择公司/岗位
- 不存储任何用户数据，仅当次请求透传
"""
import re
import time
from typing import Optional, Tuple, List, Dict

import httpx

from . import config

# token 缓存：{token, expire_at}
_token_cache: Dict[str, float] = {}


class FeishuError(Exception):
    def __init__(self, message: str, code: int = 500):
        super().__init__(message)
        self.message = message
        self.code = code


def _get_tenant_access_token(app_id: Optional[str] = None, app_secret: Optional[str] = None) -> str:
    """
    获取 tenant_access_token，带内存缓存。
    优先使用传入的 app_id/app_secret（前端用户自带凭证），否则回退到 .env 配置。
    """
    # 缓存 key 按凭证区分，避免不同用户串 token
    aid = app_id or config.FEISHU_APP_ID
    asec = app_secret or config.FEISHU_APP_SECRET
    cache_key = f"token_{aid}" if aid else "token"

    cached = _token_cache.get(cache_key)
    if cached and _token_cache.get(cache_key + "_expire", 0) > time.time() + 60:
        return cached

    if not aid or not asec:
        raise FeishuError(
            "未提供飞书应用凭证（App ID / App Secret），请在弹窗中填写或在服务端 .env 中配置",
            code=503,
        )

    url = config.FEISHU_BASE_URL.rstrip("/") + "/open-apis/auth/v3/tenant_access_token/internal"
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json={
                "app_id": aid,
                "app_secret": asec,
            })
    except Exception as e:
        raise FeishuError(f"飞书接口请求失败: {e}", code=502)

    if resp.status_code != 200:
        raise FeishuError(f"获取飞书 token 失败: HTTP {resp.status_code}", code=502)

    data = resp.json()
    if data.get("code") != 0:
        raise FeishuError(f"获取飞书 token 失败: {data.get('msg')}", code=502)

    token = data["tenant_access_token"]
    expire = data.get("expire", 7200)
    _token_cache[cache_key] = token
    _token_cache[cache_key + "_expire"] = time.time() + expire
    return token


def parse_bitable_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    从飞书多维表格链接解析 app_token 和 table_id。
    支持格式：
      https://xxx.feishu.cn/base/{app_token}?table={table_id}
      https://xxx.feishu.cn/base/{app_token}
    返回 (app_token, table_id)，table_id 可能为 None。
    """
    if not url:
        return None, None
    # 提取 /base/ 后的 token
    m = re.search(r"/base/([A-Za-z0-9]+)", url)
    app_token = m.group(1) if m else None
    # 提取 table 参数
    m2 = re.search(r"[?&]table=([A-Za-z0-9]+)", url)
    table_id = m2.group(1) if m2 else None
    return app_token, table_id


def list_tables(app_token: str, app_id: Optional[str] = None, app_secret: Optional[str] = None) -> List[Dict]:
    """列出多维表格下所有数据表。"""
    token = _get_tenant_access_token(app_id, app_secret)
    url = f"{config.FEISHU_BASE_URL.rstrip('/')}/open-apis/bitable/v1/apps/{app_token}/tables"
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {token}"})
    except Exception as e:
        raise FeishuError(f"请求飞书失败: {e}", code=502)

    data = resp.json()
    if data.get("code") != 0:
        raise FeishuError(f"读取数据表失败: {data.get('msg')}", code=502)
    return data.get("data", {}).get("items", [])


def list_records(app_token: str, table_id: str, app_id: Optional[str] = None, app_secret: Optional[str] = None, page_size: int = 100) -> List[Dict]:
    """读取多维表格指定数据表的所有记录。"""
    token = _get_tenant_access_token(app_id, app_secret)
    base_url = f"{config.FEISHU_BASE_URL.rstrip('/')}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    all_records = []
    page_token = None

    while True:
        params = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(base_url, headers={"Authorization": f"Bearer {token}"}, params=params)
        except Exception as e:
            raise FeishuError(f"请求飞书失败: {e}", code=502)

        data = resp.json()
        if data.get("code") != 0:
            raise FeishuError(f"读取记录失败: {data.get('msg')}", code=502)

        items = data.get("data", {}).get("items", [])
        all_records.extend(items)
        page_token = data.get("data", {}).get("page_token")
        if not page_token or not data.get("data", {}).get("has_more"):
            break

    return all_records


def extract_company_position(records: List[Dict]) -> List[Dict]:
    """
    从记录中提取公司和岗位字段。
    自动识别包含"公司"/"企业"/"岗位"/"职位"等关键词的列。
    返回 [{"company": ..., "position": ...}, ...]
    """
    result = []
    # 公司/岗位关键词映射
    company_keys = ["公司", "企业", "单位", "投递公司", "目标公司", "company"]
    position_keys = ["岗位", "职位", "投递岗位", "目标岗位", "岗位名称", "position", "job"]

    for rec in records:
        fields = rec.get("fields", {})
        company = ""
        position = ""
        for k, v in fields.items():
            k_lower = str(k).lower()
            # 取第一个匹配的值（可能是列表或字符串）
            val = v[0] if isinstance(v, list) and v else v
            val = str(val).strip() if val is not None else ""
            if not company and any(ck in k_lower for ck in company_keys):
                company = val
            if not position and any(pk in k_lower for pk in position_keys):
                position = val
        if company or position:
            result.append({"company": company, "position": position})
    return result
