import logging
import sys
from sqlalchemy.orm import Session
from db.models import SystemLog, get_db

class DatabaseLogHandler(logging.Handler):
    """将日志写入数据库的 Handler"""
    
    def __init__(self):
        super().__init__()
    
    def emit(self, record):
        try:
            target_uid = getattr(record, 'target_uid', None)
            
            from db.models import SessionLocal
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

def setup_logging():
    """配置日志系统"""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    db_handler = DatabaseLogHandler()
    db_handler.setLevel(logging.INFO)
    db_handler.setFormatter(formatter)
    logger.addHandler(db_handler)
    
    return logger
