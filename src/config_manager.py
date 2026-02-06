#!/usr/bin/env python3
"""
配置管理模块 - 集中加载所有配置
"""

import os
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# 配置目录
CONFIG_DIR = Path(__file__).parent.parent / "config"


class ConfigManager:
    """配置管理器（单例）"""
    
    _instance = None
    _prompts: Dict[str, Any] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def load_prompts(cls) -> Dict[str, Any]:
        """加载提示词配置"""
        if cls._prompts is not None:
            return cls._prompts
        
        prompts_file = CONFIG_DIR / "prompts.yaml"
        
        if not prompts_file.exists():
            logger.warning(f"提示词配置文件不存在: {prompts_file}")
            return cls._get_default_prompts()
        
        try:
            with open(prompts_file, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            
            cls._prompts = config
            logger.info(f"已加载提示词配置: {len(config.get('templates', {}))} 个模板")
            return cls._prompts
            
        except Exception as e:
            logger.error(f"加载提示词配置失败: {e}")
            return cls._get_default_prompts()
    
    @classmethod
    def get_prompt_template(cls, template_id: str) -> Optional[Dict[str, str]]:
        """
        获取指定提示词模板
        
        Args:
            template_id: 模板ID，如 'standard', 'value', 'trading'
            
        Returns:
            dict: 包含 name, system_prompt, analysis_prompt 的字典
        """
        prompts = cls.load_prompts()
        templates = prompts.get('templates', {})
        
        template = templates.get(template_id)
        if template:
            return {
                'id': template_id,
                'name': template.get('name', template_id),
                'system_prompt': template.get('system_prompt', ''),
                'analysis_prompt': template.get('analysis_prompt', '')
            }
        
        # 返回默认模板
        defaults = prompts.get('defaults', {})
        return {
            'id': 'default',
            'name': '默认模板',
            'system_prompt': defaults.get('system_prompt', ''),
            'analysis_prompt': defaults.get('analysis_prompt', '')
        }
    
    @classmethod
    def list_prompt_templates(cls) -> list:
        """列出所有可用的提示词模板"""
        prompts = cls.load_prompts()
        templates = prompts.get('templates', {})
        
        return [
            {'id': key, 'name': value.get('name', key)}
            for key, value in templates.items()
        ]
    
    @classmethod
    def _get_default_prompts(cls) -> Dict[str, Any]:
        """获取默认提示词配置"""
        return {
            'templates': {},
            'defaults': {
                'system_prompt': '你是一位专业的投资行为分析师。',
                'analysis_prompt': '''请分析以下用户 "{user_name}" 的投资风格和发言特征：

{content}

请从以下几个方面进行分析，并以 JSON 格式返回：
{{
    "summary": "100字以内的整体评价",
    "investment_style": "投资风格",
    "risk_preference": "风险偏好",
    "focus_areas": [],
    "key_stocks": [],
    "sentiment": "整体情绪倾向",
    "characteristics": [],
    "recommendation": "建议"
}}'''
            }
        }


# 便捷函数
def get_prompt_template(template_id: str) -> Optional[Dict[str, str]]:
    """获取提示词模板"""
    return ConfigManager.get_prompt_template(template_id)


def list_prompt_templates() -> list:
    """列出所有模板"""
    return ConfigManager.list_prompt_templates()


def load_prompts() -> Dict[str, Any]:
    """加载所有提示词配置"""
    return ConfigManager.load_prompts()
