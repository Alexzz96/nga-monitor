#!/usr/bin/env python3
"""
限流器模块 - 防止 API 调用过于频繁
支持 Discord Webhook 和 AI API 的限流控制
"""

import asyncio
import time
import logging
from typing import Optional
from dataclasses import dataclass
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """限流配置"""
    requests_per_second: float = 1.0  # 每秒请求数
    requests_per_minute: float = 30.0  # 每分钟请求数
    burst_size: int = 3  # 突发请求数


class RateLimiter:
    """
    令牌桶限流器
    
    支持两种限流维度：
    - 瞬时限流（每秒请求数）
    - 长期限流（每分钟请求数）
    """
    
    def __init__(self, config: RateLimitConfig = None, name: str = "default"):
        self.config = config or RateLimitConfig()
        self.name = name
        
        # 瞬时令牌桶
        self._tokens = self.config.burst_size
        self._last_update = time.time()
        self._token_lock = asyncio.Lock()
        
        # 长期请求记录（滑动窗口）
        self._request_times: deque = deque()
        self._window_lock = asyncio.Lock()
    
    async def acquire(self, timeout: Optional[float] = None) -> bool:
        """
        获取请求许可
        
        Args:
            timeout: 等待超时时间（秒），None 表示无限等待
            
        Returns:
            bool: 是否获得许可
        """
        start_time = time.time()
        
        while True:
            can_proceed = await self._try_acquire()
            if can_proceed:
                return True
            
            if timeout is not None:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    logger.warning(f"[{self.name}] 限流等待超时")
                    return False
            
            # 等待一小段时间后重试
            await asyncio.sleep(0.1)
    
    async def _try_acquire(self) -> bool:
        """尝试获取许可"""
        async with self._token_lock:
            # 更新令牌
            now = time.time()
            elapsed = now - self._last_update
            self._tokens = min(
                self.config.burst_size,
                self._tokens + elapsed * self.config.requests_per_second
            )
            self._last_update = now
            
            # 检查瞬时令牌
            if self._tokens < 1:
                return False
            
            # 检查长期限流
            async with self._window_lock:
                now = time.time()
                window_start = now - 60  # 60 秒窗口
                
                # 移除窗口外的记录
                while self._request_times and self._request_times[0] < window_start:
                    self._request_times.popleft()
                
                # 检查是否超过每分钟限制
                if len(self._request_times) >= self.config.requests_per_minute:
                    return False
                
                # 消耗令牌并记录请求
                self._tokens -= 1
                self._request_times.append(now)
                
                logger.debug(f"[{self.name}] 请求通过，当前令牌: {self._tokens:.2f}, "
                           f"60秒窗口请求数: {len(self._request_times)}")
                return True
    
    async def __aenter__(self):
        await self.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False
    
    def get_stats(self) -> dict:
        """获取限流器统计信息"""
        now = time.time()
        window_start = now - 60
        
        requests_in_window = sum(1 for t in self._request_times if t >= window_start)
        
        return {
            'name': self.name,
            'tokens': round(self._tokens, 2),
            'requests_in_60s': requests_in_window,
            'config': {
                'requests_per_second': self.config.requests_per_second,
                'requests_per_minute': self.config.requests_per_minute,
                'burst_size': self.config.burst_size
            }
        }


# 全局限流器实例
_discord_limiter: Optional[RateLimiter] = None
_ai_limiter: Optional[RateLimiter] = None


def get_discord_limiter() -> RateLimiter:
    """获取 Discord Webhook 限流器"""
    global _discord_limiter
    if _discord_limiter is None:
        _discord_limiter = RateLimiter(
            config=RateLimitConfig(
                requests_per_second=0.5,    # 每2秒1个请求
                requests_per_minute=30,      # 每分钟最多30个
                burst_size=2                 # 最多突发2个
            ),
            name="discord_webhook"
        )
    return _discord_limiter


def get_ai_limiter() -> RateLimiter:
    """获取 AI API 限流器"""
    global _ai_limiter
    if _ai_limiter is None:
        _ai_limiter = RateLimiter(
            config=RateLimitConfig(
                requests_per_second=0.2,     # 每5秒1个请求
                requests_per_minute=20,       # 每分钟最多20个
                burst_size=1                  # 不突发
            ),
            name="ai_api"
        )
    return _ai_limiter


def get_limiter_stats() -> dict:
    """获取所有限流器统计"""
    stats = {}
    if _discord_limiter:
        stats['discord'] = _discord_limiter.get_stats()
    if _ai_limiter:
        stats['ai'] = _ai_limiter.get_stats()
    return stats
