#!/usr/bin/env python3
"""
浏览器连接池模块 - 复用 Playwright 浏览器实例
大幅减少内存占用和启动时间
"""

import asyncio
import logging
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext

logger = logging.getLogger(__name__)


class BrowserPool:
    """
    Playwright 浏览器连接池（单例模式）
    
    使用方式:
        pool = BrowserPool.get_instance()
        context = await pool.get_context(storage_state_path)
        page = await context.new_page()
        ...
        await pool.release_context(context)
    """
    
    _instance: Optional['BrowserPool'] = None
    _lock = asyncio.Lock()
    
    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._contexts: dict[str, BrowserContext] = {}
        self._context_refs: dict[str, int] = {}  # 引用计数
        self._initialized = False
        self._browser_args = [
            '--disable-dev-shm-usage',
            '--disable-gpu',
            '--disable-setuid-sandbox',
            '--no-sandbox',
            '--disable-features=site-per-process',
            '--disable-web-security',
            '--disable-features=IsolateOrigins,site-per-process',
        ]
    
    @classmethod
    def get_instance(cls) -> 'BrowserPool':
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    async def _ensure_initialized(self):
        """确保浏览器已初始化"""
        if self._initialized:
            return
        
        async with self._lock:
            if self._initialized:
                return
            
            logger.info("[BrowserPool] 初始化 Playwright...")
            self._playwright = await async_playwright().start()
            
            logger.info("[BrowserPool] 启动 Chromium...")
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=self._browser_args
            )
            
            self._initialized = True
            logger.info("[BrowserPool] 浏览器初始化完成")
    
    async def get_context(self, storage_state_path: str) -> BrowserContext:
        """
        获取浏览器上下文
        
        Args:
            storage_state_path: storage state 文件路径
            
        Returns:
            BrowserContext: 浏览器上下文
        """
        await self._ensure_initialized()
        
        # 使用路径作为 key 复用相同 storage state 的 context
        key = storage_state_path
        
        async with self._lock:
            if key in self._contexts:
                self._context_refs[key] += 1
                logger.debug(f"[BrowserPool] 复用 context: {key}, refs={self._context_refs[key]}")
                return self._contexts[key]
        
        # 创建新的 context
        logger.info(f"[BrowserPool] 创建新 context: {key}")
        
        import json
        try:
            with open(storage_state_path, "r") as f:
                storage_state = json.load(f)
        except Exception as e:
            logger.error(f"[BrowserPool] 读取 storage state 失败: {e}")
            storage_state = {}
        
        context = await self._browser.new_context(
            storage_state=storage_state,
            viewport={'width': 1280, 'height': 720},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        )
        
        async with self._lock:
            self._contexts[key] = context
            self._context_refs[key] = 1
        
        return context
    
    async def release_context(self, context: BrowserContext, save_state_path: Optional[str] = None):
        """
        释放浏览器上下文
        
        Args:
            context: 要释放的上下文
            save_state_path: 如果需要保存 state，提供路径
        """
        async with self._lock:
            # 找到对应的 key
            key = None
            for k, ctx in self._contexts.items():
                if ctx == context:
                    key = k
                    break
            
            if key is None:
                logger.warning("[BrowserPool] 释放未知的 context")
                return
            
            self._context_refs[key] -= 1
            ref_count = self._context_refs[key]
            
            logger.debug(f"[BrowserPool] 释放 context: {key}, refs={ref_count}")
            
            if ref_count <= 0:
                # 引用计数为 0，关闭 context
                logger.info(f"[BrowserPool] 关闭 context: {key}")
                
                try:
                    if save_state_path:
                        await context.storage_state(path=save_state_path)
                        logger.debug(f"[BrowserPool] 保存 storage state: {save_state_path}")
                except Exception as e:
                    logger.error(f"[BrowserPool] 保存 storage state 失败: {e}")
                
                try:
                    await context.close()
                except Exception as e:
                    logger.error(f"[BrowserPool] 关闭 context 失败: {e}")
                
                del self._contexts[key]
                del self._context_refs[key]
    
    async def close(self):
        """关闭浏览器池（应用退出时调用）"""
        logger.info("[BrowserPool] 关闭浏览器池...")
        
        async with self._lock:
            # 关闭所有 context
            for key, context in self._contexts.items():
                try:
                    await context.close()
                    logger.debug(f"[BrowserPool] 关闭 context: {key}")
                except Exception as e:
                    logger.error(f"[BrowserPool] 关闭 context 失败: {key}, {e}")
            
            self._contexts.clear()
            self._context_refs.clear()
            
            # 关闭浏览器
            if self._browser:
                try:
                    await self._browser.close()
                    logger.info("[BrowserPool] 浏览器已关闭")
                except Exception as e:
                    logger.error(f"[BrowserPool] 关闭浏览器失败: {e}")
                self._browser = None
            
            # 停止 playwright
            if self._playwright:
                try:
                    await self._playwright.stop()
                    logger.info("[BrowserPool] Playwright 已停止")
                except Exception as e:
                    logger.error(f"[BrowserPool] 停止 Playwright 失败: {e}")
                self._playwright = None
            
            self._initialized = False
            BrowserPool._instance = None
    
    @property
    def is_initialized(self) -> bool:
        """检查是否已初始化"""
        return self._initialized
    
    def get_stats(self) -> dict:
        """获取连接池统计信息"""
        return {
            'initialized': self._initialized,
            'contexts_count': len(self._contexts),
            'contexts': {k: self._context_refs.get(k, 0) for k in self._contexts.keys()}
        }


# 便捷函数
async def get_browser_pool() -> BrowserPool:
    """获取浏览器连接池实例"""
    return BrowserPool.get_instance()


async def close_browser_pool():
    """关闭浏览器连接池"""
    pool = BrowserPool.get_instance()
    await pool.close()


class ManagedBrowserContext:
    """
    异步上下文管理器，自动管理 context 生命周期
    
    使用方式:
        async with ManagedBrowserContext(storage_state_path) as context:
            page = await context.new_page()
            ...
    """
    
    def __init__(self, storage_state_path: str, save_state_on_exit: bool = True):
        self.storage_state_path = storage_state_path
        self.save_state_on_exit = save_state_on_exit
        self.context: Optional[BrowserContext] = None
        self.pool: Optional[BrowserPool] = None
    
    async def __aenter__(self) -> BrowserContext:
        self.pool = BrowserPool.get_instance()
        self.context = await self.pool.get_context(self.storage_state_path)
        return self.context
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.pool and self.context:
            save_path = self.storage_state_path if self.save_state_on_exit else None
            await self.pool.release_context(self.context, save_path)
        return False  # 不捕获异常
