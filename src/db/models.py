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
    keywords = Column(Text, default='')  # 关键词过滤，逗号分隔
    keyword_mode = Column(String(10), default='ANY')  # ANY(任一), ALL(全部), REGEX(正则)
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
            'keywords': self.keywords or '',
            'keyword_mode': self.keyword_mode or 'ANY',
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


class ReplyArchive(Base):
    """回复存档 - 存储所有抓取到的回复内容"""
    __tablename__ = 'reply_archives'
    
    id = Column(Integer, primary_key=True)
    target_id = Column(Integer, ForeignKey('monitor_targets.id'), nullable=False, index=True)
    pid = Column(String(50), nullable=False, index=True)
    tid = Column(String(50), index=True)
    topic_title = Column(String(300))
    content_full = Column(Text)  # 完整回复内容
    quote_content = Column(Text)  # 引用内容
    main_content = Column(Text)   # 主内容
    forum = Column(String(100))   # 版块
    post_date = Column(String(50), index=True)  # 发帖时间（加索引用于AI分析筛选）
    url = Column(String(500))     # 链接
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    
    # 情绪分析字段
    sentiment = Column(String(20), index=True)  # positive/negative/neutral
    sentiment_score = Column(Float)  # -1.0 to 1.0
    sentiment_analyzed_at = Column(DateTime)  # 分析时间
    
    # 关联
    target = relationship("MonitorTarget")
    
    def to_dict(self):
        return {
            'id': self.id,
            'target_id': self.target_id,
            'pid': self.pid,
            'tid': self.tid,
            'topic_title': self.topic_title,
            'content_full': self.content_full,
            'quote_content': self.quote_content,
            'main_content': self.main_content,
            'forum': self.forum,
            'post_date': self.post_date,
            'url': self.url,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'sentiment': self.sentiment,
            'sentiment_score': self.sentiment_score,
            'sentiment_analyzed_at': self.sentiment_analyzed_at.isoformat() if self.sentiment_analyzed_at else None
        }


class SentimentAnalysis(Base):
    """情绪分析汇总 - 按日期聚合的情绪数据"""
    __tablename__ = 'sentiment_analysis'
    
    id = Column(Integer, primary_key=True)
    target_id = Column(Integer, ForeignKey('monitor_targets.id'), nullable=False, index=True)
    date = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    
    # 统计数据
    total_replies = Column(Integer, default=0)
    positive_count = Column(Integer, default=0)
    neutral_count = Column(Integer, default=0)
    negative_count = Column(Integer, default=0)
    
    # 情绪指数 (-1.0 to 1.0)
    sentiment_index = Column(Float, default=0.0)
    
    # 关键词情绪 {keyword: score}
    keyword_sentiment = Column(Text)  # JSON格式存储
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # 关联
    target = relationship("MonitorTarget")
    
    def to_dict(self):
        import json
        return {
            'id': self.id,
            'target_id': self.target_id,
            'target_name': self.target.name if self.target else None,
            'date': self.date,
            'total_replies': self.total_replies,
            'positive_count': self.positive_count,
            'neutral_count': self.neutral_count,
            'negative_count': self.negative_count,
            'sentiment_index': self.sentiment_index,
            'keyword_sentiment': json.loads(self.keyword_sentiment) if self.keyword_sentiment else {},
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class ArchiveTask(Base):
    """归档任务追踪"""
    __tablename__ = 'archive_tasks'
    
    id = Column(Integer, primary_key=True)
    target_id = Column(Integer, ForeignKey('monitor_targets.id'), nullable=False, index=True)
    status = Column(String(20), default='pending', index=True)  # pending/running/completed/failed
    total_pages = Column(Integer, default=0)
    completed_pages = Column(Integer, default=0)
    total_replies = Column(Integer, default=0)
    archived_count = Column(Integer, default=0)
    skipped_count = Column(Integer, default=0)
    error_message = Column(Text)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime)
    
    # 关联
    target = relationship("MonitorTarget")
    
    def to_dict(self):
        return {
            'id': self.id,
            'target_id': self.target_id,
            'target_name': self.target.name if self.target else None,
            'status': self.status,
            'total_pages': self.total_pages,
            'completed_pages': self.completed_pages,
            'total_replies': self.total_replies,
            'archived_count': self.archived_count,
            'skipped_count': self.skipped_count,
            'error_message': self.error_message,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'progress_percent': round(self.completed_pages / self.total_pages * 100, 1) if self.total_pages > 0 else 0
        }


class AIAnalysisReport(Base):
    """AI 分析报告"""
    __tablename__ = 'ai_analysis_reports'
    
    id = Column(Integer, primary_key=True)
    target_id = Column(Integer, ForeignKey('monitor_targets.id'), nullable=False, index=True)
    analysis_type = Column(String(20), nullable=False, index=True)  # 'single' 或 'compare'
    time_range = Column(String(20), nullable=False)  # 'week', 'month', 'all'
    start_date = Column(String(10))  # YYYY-MM-DD
    end_date = Column(String(10))    # YYYY-MM-DD
    report_content = Column(Text)    # 完整报告内容
    summary = Column(Text)           # 简短摘要
    style_tags = Column(String(500)) # 风格标签，JSON 格式
    keywords = Column(String(500))   # 关键词，JSON 格式
    sentiment_score = Column(Integer)  # 情感分数 -100 到 100
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    # 关联
    target = relationship("MonitorTarget")
    
    def to_dict(self):
        return {
            'id': self.id,
            'target_id': self.target_id,
            'analysis_type': self.analysis_type,
            'time_range': self.time_range,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'summary': self.summary,
            'style_tags': self.style_tags,
            'keywords': self.keywords,
            'sentiment_score': self.sentiment_score,
            'created_at': self.created_at.isoformat() if self.created_at else None
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
    
    @staticmethod
    def get_ai_config(db):
        """获取 AI 配置"""
        configs = db.query(Config).filter(Config.key.like('ai_%')).all()
        result = {
            'provider': 'kimi',
            'base_url': 'https://api.moonshot.cn/v1',
            'api_key': '',
            'model': 'moonshot-v1-8k',
            'system_prompt': '你是一位专业的投资行为分析师，擅长从论坛帖子中分析用户的投资风格、关注领域和行为特征。请用中文回答。',
            'analysis_prompt': '''请分析以下用户 "{user_name}" 的投资风格和发言特征：

{content}

请从以下几个方面进行分析，并以 JSON 格式返回：
{{
    "summary": "100字以内的整体评价",
    "investment_style": "投资风格（如：长线价值投资/短线波段操作/均衡配置等）",
    "risk_preference": "风险偏好（保守/稳健/激进）",
    "focus_areas": ["关注的板块1", "关注的板块2"],
    "key_stocks": ["提及的股票/基金代码"],
    "sentiment": "整体情绪倾向（乐观/中性/悲观）",
    "characteristics": ["特点1", "特点2", "特点3"],
    "recommendation": "对该用户的简要评价或建议"
}}'''
        }
        for cfg in configs:
            key = cfg.key.replace('ai_', '')
            result[key] = cfg.value
        return result
    
    @staticmethod
    def set_ai_config(db, config: dict):
        """设置 AI 配置"""
        for key, value in config.items():
            full_key = f'ai_{key}'
            cfg = db.query(Config).filter(Config.key == full_key).first()
            if cfg:
                cfg.value = value
            else:
                cfg = Config(key=full_key, value=value)
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
