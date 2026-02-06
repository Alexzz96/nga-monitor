#!/usr/bin/env python3
"""
时间段调度管理模块
支持按时间段配置不同的检查频率
"""

from datetime import datetime, time, timedelta
from typing import Optional, List, Tuple
from contextlib import contextmanager
from db.models import SessionLocal, ScheduleRule, DailySummary
import logging

logger = logging.getLogger(__name__)


@contextmanager
def get_db_session():
    """数据库会话上下文管理器（线程安全）"""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class ScheduleManager:
    """调度管理器（修复连接泄漏问题）"""
    
    def __init__(self):
        # 不再需要维护线程本地存储，使用 contextmanager 管理会话
        pass
    
    def get_active_rules(self) -> List[ScheduleRule]:
        """获取所有启用的调度规则，按优先级排序"""
        with get_db_session() as db:
            return db.query(ScheduleRule).filter(
                ScheduleRule.enabled == True
            ).order_by(ScheduleRule.priority.desc()).all()
    
    def get_current_rule(self, check_time: Optional[datetime] = None) -> Optional[ScheduleRule]:
        """
        获取当前时间适用的调度规则
        
        Returns:
            ScheduleRule or None: 当前适用的规则，如果没有则返回 None
        """
        if check_time is None:
            check_time = datetime.now()
        
        current_time = check_time.strftime('%H:%M')
        
        with get_db_session() as db:
            rules = db.query(ScheduleRule).filter(
                ScheduleRule.enabled == True
            ).order_by(ScheduleRule.priority.desc()).all()
            
            for rule in rules:
                if self._is_time_in_range(current_time, rule.start_time, rule.end_time):
                    return rule
        
        return None
    
    def _is_time_in_range(self, current: str, start: str, end: str) -> bool:
        """检查当前时间是否在范围内"""
        # 处理跨天的情况，如 22:00-06:00
        if start <= end:
            return start <= current <= end
        else:
            # 跨天，如 22:00-06:00
            return current >= start or current <= end
    
    def should_check_now(self, last_check_time: Optional[datetime] = None) -> Tuple[bool, Optional[int]]:
        """
        判断现在是否应该执行检查
        
        Returns:
            (should_check, interval): 是否应该检查，以及建议的间隔
        """
        rule = self.get_current_rule()
        
        if rule is None:
            logger.debug("当前时间没有适用的调度规则")
            return False, 60
        
        logger.debug(f"当前适用规则: {rule.name} ({rule.start_time}-{rule.end_time})")
        
        # 总结模式：只在结束时间点执行一次
        if rule.is_summary:
            return self._should_send_summary(rule, last_check_time), rule.interval_seconds or 3600
        
        # 常规模式：按间隔执行
        if rule.interval_seconds <= 0:
            return False, 60
        
        if last_check_time is None:
            return True, rule.interval_seconds
        
        elapsed = (datetime.now() - last_check_time).total_seconds()
        should_check = elapsed >= rule.interval_seconds
        
        return should_check, rule.interval_seconds
    
    def _should_send_summary(self, rule: ScheduleRule, last_check_time: Optional[datetime]) -> bool:
        """判断是否应该发送总结"""
        now = datetime.now()
        current_time = now.strftime('%H:%M')
        
        with get_db_session() as db:
            # 检查今天是否已经发送过总结
            today = now.strftime('%Y-%m-%d')
            summary_sent = db.query(DailySummary).filter(
                DailySummary.date == today,
                DailySummary.rule_id == rule.id
            ).first()
            
            if summary_sent:
                logger.debug(f"今天 {today} 已发送过 {rule.name} 的总结")
                return False
        
        # 检查是否接近结束时间（5分钟内）
        end_hour, end_min = map(int, rule.end_time.split(':'))
        end_time = now.replace(hour=end_hour, minute=end_min, second=0, microsecond=0)
        
        # 如果结束时间是明天（跨天）
        if end_time < now and rule.start_time > rule.end_time:
            end_time += timedelta(days=1)
        
        time_diff = (end_time - now).total_seconds()
        
        # 在结束时间点前后5分钟内触发
        if -300 <= time_diff <= 300:
            logger.info(f"触发 {rule.name} 总结推送")
            return True
        
        return False
    
    def mark_summary_sent(self, rule_id: int, target_id: int, new_count: int = 0):
        """标记总结已发送"""
        with get_db_session() as db:
            today = datetime.now().strftime('%Y-%m-%d')
            summary = DailySummary(
                date=today,
                target_id=target_id,
                rule_id=rule_id,
                new_count=new_count
            )
            db.add(summary)
            db.commit()
    
    def get_next_check_time(self) -> Optional[datetime]:
        """获取下次应该检查的时间"""
        rule = self.get_current_rule()
        if rule is None:
            # 查找下一个即将开始的规则
            return self._get_next_rule_start_time()
        
        if rule.is_summary:
            # 总结模式：返回结束时间
            return self._get_rule_end_datetime(rule)
        
        # 常规模式：当前时间 + 间隔
        return datetime.now() + timedelta(seconds=rule.interval_seconds)
    
    def _get_next_rule_start_time(self) -> Optional[datetime]:
        """获取下一个规则的开始时间"""
        now = datetime.now()
        current_time = now.strftime('%H:%M')
        
        with get_db_session() as db:
            rules = db.query(ScheduleRule).filter(
                ScheduleRule.enabled == True
            ).order_by(ScheduleRule.priority.desc()).all()
            
            for rule in rules:
                if rule.start_time > current_time:
                    hour, minute = map(int, rule.start_time.split(':'))
                    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            # 如果没有找到，返回第一个规则的明天时间
            if rules:
                hour, minute = map(int, rules[0].start_time.split(':'))
                tomorrow = now + timedelta(days=1)
                return tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        return None
    
    def _get_rule_end_datetime(self, rule: ScheduleRule) -> datetime:
        """获取规则的结束时间"""
        now = datetime.now()
        hour, minute = map(int, rule.end_time.split(':'))
        end_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # 处理跨天
        if end_time < now and rule.start_time > rule.end_time:
            end_time += timedelta(days=1)
        
        return end_time
    
    def get_current_status(self) -> dict:
        """获取当前调度状态"""
        rule = self.get_current_rule()
        next_check = self.get_next_check_time()
        
        if rule is None:
            return {
                'current_rule': None,
                'status': '等待中',
                'next_check': next_check.isoformat() if next_check else None
            }
        
        return {
            'current_rule': rule.to_dict() if hasattr(rule, 'to_dict') else {
                'id': rule.id,
                'name': rule.name,
                'start_time': rule.start_time,
                'end_time': rule.end_time,
                'interval_seconds': rule.interval_seconds,
                'is_summary': rule.is_summary
            },
            'status': '总结模式' if rule.is_summary else f'每{rule.interval_seconds}秒检查',
            'next_check': next_check.isoformat() if next_check else None
        }
