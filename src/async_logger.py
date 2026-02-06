#!/usr/bin/env python3
"""
异步日志处理器 - 使用队列避免阻塞主线程
"""

import asyncio
import logging
import threading
from queue import Queue, Empty
from db.models import SessionLocal, SystemLog


class AsyncDatabaseLogHandler(logging.Handler):
    """
    异步数据库日志处理器
    
    使用独立线程处理日志写入，避免阻塞主线程
    """
    
    def __init__(self, flush_interval=5, max_queue_size=1000, batch_size=10):
        super().__init__()
        self.flush_interval = flush_interval  # 批量写入间隔（秒）
        self.max_queue_size = max_queue_size  # 队列最大长度
        self.batch_size = batch_size  # 每批写入数量
        
        self._queue = Queue(maxsize=max_queue_size)
        self._thread: threading.Thread = None
        self._stop_event = threading.Event()
        self._started = False
        self._lock = threading.Lock()
    
    def emit(self, record):
        """提交日志到队列（非阻塞）"""
        try:
            # 如果队列满了，丢弃最旧的日志
            if self._queue.full():
                try:
                    self._queue.get_nowait()
                except Empty:
                    pass
            
            # 格式化日志并放入队列
            log_data = {
                'level': record.levelname,
                'message': self.format(record),
                'target_uid': getattr(record, 'target_uid', None),
                'created_at': record.created
            }
            self._queue.put_nowait(log_data)
            
            # 确保后台线程已启动
            self._ensure_started()
            
        except Exception:
            self.handleError(record)
    
    def _ensure_started(self):
        """确保后台线程已启动"""
        with self._lock:
            if not self._started:
                self._thread = threading.Thread(target=self._worker, daemon=True)
                self._thread.start()
                self._started = True
    
    def _worker(self):
        """后台工作线程：批量写入数据库"""
        while not self._stop_event.is_set():
            batch = []
            
            # 收集一批日志或等待超时
            try:
                # 等待第一条日志
                log_data = self._queue.get(timeout=self.flush_interval)
                batch.append(log_data)
                
                # 继续收集更多日志
                for _ in range(self.batch_size - 1):
                    try:
                        log_data = self._queue.get_nowait()
                        batch.append(log_data)
                    except Empty:
                        break
                        
            except Empty:
                # 超时，检查是否需要停止
                continue
            
            # 批量写入数据库
            if batch:
                self._flush_batch(batch)
        
        # 关闭前刷新剩余日志
        self._flush_remaining()
    
    def _flush_batch(self, batch):
        """批量写入数据库"""
        if not batch:
            return
        
        try:
            db = SessionLocal()
            try:
                logs = [
                    SystemLog(
                        level=log['level'],
                        message=log['message'],
                        target_uid=log['target_uid']
                    )
                    for log in batch
                ]
                db.bulk_save_objects(logs)
                db.commit()
            finally:
                db.close()
        except Exception as e:
            # 写入失败，打印到 stderr
            import sys
            print(f"[AsyncDatabaseLogHandler] 写入日志失败: {e}", file=sys.stderr)
    
    def _flush_remaining(self):
        """刷新队列中剩余的日志"""
        batch = []
        while not self._queue.empty():
            try:
                batch.append(self._queue.get_nowait())
                if len(batch) >= self.batch_size:
                    self._flush_batch(batch)
                    batch = []
            except Empty:
                break
        
        if batch:
            self._flush_batch(batch)
    
    def close(self):
        """关闭处理器，等待队列清空"""
        self._stop_event.set()
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        
        super().close()


class SyncDatabaseLogHandler(logging.Handler):
    """
    同步数据库日志处理器（备用方案）
    
    只在 WARNING 及以上级别使用，减少阻塞
    """
    
    def __init__(self):
        super().__init__()
        self.setLevel(logging.WARNING)  # 只记录警告及以上
    
    def emit(self, record):
        try:
            target_uid = getattr(record, 'target_uid', None)
            
            db = SessionLocal()
            try:
                log_entry = SystemLog(
                    level=record.levelname,
                    message=self.format(record),
                    target_uid=target_uid
                )
                db.add(log_entry)
                db.commit()
            finally:
                db.close()
        except Exception:
            self.handleError(record)


# 全局处理器实例（单例）
_async_handler: AsyncDatabaseLogHandler = None


def get_async_handler() -> AsyncDatabaseLogHandler:
    """获取异步日志处理器实例（单例）"""
    global _async_handler
    if _async_handler is None:
        _async_handler = AsyncDatabaseLogHandler(
            flush_interval=5,    # 5秒批量写入一次
            max_queue_size=1000,  # 队列最多1000条
            batch_size=10         # 每批最多10条
        )
    return _async_handler


def close_async_handler():
    """关闭异步日志处理器"""
    global _async_handler
    if _async_handler:
        _async_handler.close()
        _async_handler = None
