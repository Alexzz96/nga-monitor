from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime, timezone, timedelta
import os

Base = declarative_base()

DB_PATH = os.getenv('DB_PATH', '/app/data/nga_monitor.db')
engine = create_engine(f'sqlite:///{DB_PATH}', echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class MonitorTarget(Base):
    """监控目标"""
    __tablename__ = 'monitor_targets'
    
    id = Column(Integer, primary_key=True)
    uid = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(100), default='')
    url = Column(String(500), nullable=False)
    enabled = Column(Boolean, default=True)
    check_interval = Column(Integer, default=60)  # 秒
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # 关联
    sent_records = relationship("SentRecord", back_populates="target", cascade="all, delete-orphan")
    
    def to_dict(self):
        return {
            'id': self.id,
            'uid': self.uid,
            'name': self.name,
            'url': self.url,
            'enabled': self.enabled,
            'check_interval': self.check_interval,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

class SentRecord(Base):
    """已发送记录"""
    __tablename__ = 'sent_records'
    
    id = Column(Integer, primary_key=True)
    target_id = Column(Integer, ForeignKey('monitor_targets.id'), nullable=False)
    pid = Column(String(50), nullable=False, index=True)
    tid = Column(String(50))
    topic_title = Column(String(300))
    content_preview = Column(Text)
    sent_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    success = Column(Boolean, default=True)
    error_message = Column(Text)
    
    # 关联
    target = relationship("MonitorTarget", back_populates="sent_records")
    
    def to_dict(self):
        return {
            'id': self.id,
            'target_id': self.target_id,
            'pid': self.pid,
            'tid': self.tid,
            'topic_title': self.topic_title,
            'content_preview': self.content_preview[:200] if self.content_preview else '',
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'success': self.success
        }

class SystemLog(Base):
    """系统日志"""
    __tablename__ = 'system_logs'
    
    id = Column(Integer, primary_key=True)
    level = Column(String(20), default='INFO', index=True)  # DEBUG, INFO, WARNING, ERROR
    message = Column(Text, nullable=False)
    target_uid = Column(String(20), index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'level': self.level,
            'message': self.message,
            'target_uid': self.target_uid,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class ScheduleRule(Base):
    """时间段调度规则"""
    __tablename__ = 'schedule_rules'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), default='')  # 规则名称，如"夜间模式"
    start_time = Column(String(5), nullable=False)  # HH:MM 格式，如 "00:00"
    end_time = Column(String(5), nullable=False)    # HH:MM 格式，如 "08:00"
    interval_seconds = Column(Integer, default=60)   # 执行间隔秒数，0表示不执行
    is_summary = Column(Boolean, default=False)      # 是否是总结模式（只在结束时间点执行一次）
    enabled = Column(Boolean, default=True)
    priority = Column(Integer, default=0)            # 优先级，数字大的优先
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'interval_seconds': self.interval_seconds,
            'is_summary': self.is_summary,
            'enabled': self.enabled,
            'priority': self.priority
        }

class DailySummary(Base):
    """每日总结发送记录"""
    __tablename__ = 'daily_summaries'
    
    id = Column(Integer, primary_key=True)
    date = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    target_id = Column(Integer, ForeignKey('monitor_targets.id'), nullable=False)
    rule_id = Column(Integer, ForeignKey('schedule_rules.id'), nullable=False)
    sent_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    new_count = Column(Integer, default=0)  # 该时段新回复数量
    
    def to_dict(self):
        return {
            'id': self.id,
            'date': self.date,
            'target_id': self.target_id,
            'rule_id': self.rule_id,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'new_count': self.new_count
        }

class Config(Base):
    """系统配置"""
    __tablename__ = 'config'
    
    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    @staticmethod
    def get_webhook(db):
        """获取 webhook URL"""
        cfg = db.query(Config).filter(Config.key == 'discord_webhook').first()
        return cfg.value if cfg else os.getenv('DISCORD_WEBHOOK_URL', '')
    
    @staticmethod
    def set_webhook(db, url):
        """设置 webhook URL"""
        cfg = db.query(Config).filter(Config.key == 'discord_webhook').first()
        if cfg:
            cfg.value = url
        else:
            cfg = Config(key='discord_webhook', value=url)
            db.add(cfg)
        db.commit()

def init_db():
    """初始化数据库"""
    Base.metadata.create_all(bind=engine)
    
    # 初始化默认数据
    db = SessionLocal()
    try:
        # 检查是否有监控目标，没有则创建默认的
        if db.query(MonitorTarget).count() == 0:
            default_target = MonitorTarget(
                uid='557398',
                name='默认监控',
                url='https://nga.178.com/thread.php?searchpost=1&authorid=557398',
                enabled=True,
                check_interval=60
            )
            db.add(default_target)
            db.commit()
            print(f"✅ 创建默认监控目标: UID 557398")
        
        # 初始化 webhook
        if not Config.get_webhook(db):
            webhook = os.getenv('DISCORD_WEBHOOK_URL', '')
            if webhook:
                Config.set_webhook(db, webhook)
                print(f"✅ 初始化 webhook 配置")
        
        # 初始化默认调度规则
        if db.query(ScheduleRule).count() == 0:
            # 夜间模式：00:00-08:00，总结模式
            night_rule = ScheduleRule(
                name='夜间模式',
                start_time='00:00',
                end_time='08:00',
                interval_seconds=0,  # 不执行常规检查
                is_summary=True,
                enabled=True,
                priority=1
            )
            # 日间模式：08:00-23:59，每60秒检查
            day_rule = ScheduleRule(
                name='日间模式',
                start_time='08:00',
                end_time='23:59',
                interval_seconds=60,
                is_summary=False,
                enabled=True,
                priority=2
            )
            db.add(night_rule)
            db.add(day_rule)
            db.commit()
            print(f"✅ 创建默认调度规则: 夜间总结模式 + 日间高频模式")
    finally:
        db.close()

def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def cleanup_old_logs(days=7):
    """清理旧日志"""
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        deleted = db.query(SystemLog).filter(SystemLog.created_at < cutoff).delete()
        db.commit()
        return deleted
    finally:
        db.close()
