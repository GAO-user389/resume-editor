# -*- coding: utf-8 -*-
"""
手写 Redis 限流器（计数器 + TTL 滑动窗口近似）。
- 优先使用 Redis；若 Redis 不可达，降级为内存字典（仅本地开发，生产必须用 Redis）。
- Key 设计：
    rl:min:{ip}:{minute}       IP 分钟窗口
    rl:day:ip:{ip}:{date}      IP 日窗口
    rl:day:u:{user_id}:{date}  登录用户日窗口
    rl:day:global:{date}       全局日窗口
"""
import time
import threading
from typing import Optional, Tuple

import redis

from . import config


class RateLimiter:
    def __init__(self, redis_url: str = config.REDIS_URL):
        self._use_redis = False
        self._redis = None
        # 内存降级存储：{key: {"count": int, "expire_at": float}}
        self._mem_store: dict = {}
        self._lock = threading.Lock()

        try:
            self._redis = redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
            self._redis.ping()
            self._use_redis = True
        except Exception as e:
            # Redis 不可达，降级到内存（仅本地开发）
            print(f"[RateLimit] Redis 不可达，降级为内存限流（生产环境必须部署 Redis）: {e}")

    @property
    def use_redis(self) -> bool:
        return self._use_redis

    @staticmethod
    def _minute_key() -> str:
        return time.strftime("%Y%m%d%H%M")

    @staticmethod
    def _day_key() -> str:
        return time.strftime("%Y%m%d")

    def _incr(self, key: str, ttl_seconds: int) -> int:
        """原子递增计数器，返回递增后的值。"""
        if self._use_redis:
            try:
                pipe = self._redis.pipeline()
                pipe.incr(key)
                pipe.expire(key, ttl_seconds)
                count, _ = pipe.execute()
                return int(count)
            except Exception:
                # Redis 运行时故障，降级内存
                return self._incr_mem(key, ttl_seconds)
        return self._incr_mem(key, ttl_seconds)

    def _incr_mem(self, key: str, ttl_seconds: int) -> int:
        with self._lock:
            now = time.time()
            entry = self._mem_store.get(key)
            if not entry or entry["expire_at"] <= now:
                entry = {"count": 0, "expire_at": now + ttl_seconds}
            entry["count"] += 1
            self._mem_store[key] = entry
            return entry["count"]

    def check(self, ip: str, user_id: Optional[str] = None) -> Tuple[bool, str, int]:
        """
        统一限流检查。
        返回 (是否放行, 被拦截的窗口名, 剩余可用次数)。
        剩余次数取所有维度的最小值。
        """
        minute = self._minute_key()
        day = self._day_key()

        # 1. 全局熔断
        global_key = f"rl:day:global:{day}"
        global_count = self._incr(global_key, 86400)
        if global_count > config.RATE_GLOBAL_PER_DAY:
            return False, "global", 0

        # 2. IP 分钟窗口
        ip_min_key = f"rl:min:{ip}:{minute}"
        ip_min_count = self._incr(ip_min_key, 60)
        if ip_min_count > config.RATE_IP_PER_MINUTE:
            return False, "ip_minute", 0

        # 3. IP 日窗口（未登录用户）
        ip_day_key = f"rl:day:ip:{ip}:{day}"
        ip_day_count = self._incr(ip_day_key, 86400)
        if user_id is None:
            if ip_day_count > config.RATE_IP_PER_DAY:
                return False, "ip_day", 0
            remaining = config.RATE_IP_PER_DAY - ip_day_count
        else:
            # 登录用户：用用户日窗口
            user_day_key = f"rl:day:u:{user_id}:{day}"
            user_day_count = self._incr(user_day_key, 86400)
            if user_day_count > config.RATE_USER_PER_DAY:
                return False, "user_day", 0
            remaining = config.RATE_USER_PER_DAY - user_day_count

        # 剩余次数同时受分钟窗口约束
        min_remaining = config.RATE_IP_PER_MINUTE - ip_min_count
        remaining = min(remaining, min_remaining)
        return True, "", max(remaining, 0)


# 单例
limiter = RateLimiter()
