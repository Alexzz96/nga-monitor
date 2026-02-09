#!/usr/bin/env python3
"""
Discord Webhook 发送模块（带限流保护）
"""

import re
import httpx
from datetime import datetime, timezone
from rate_limiter import get_discord_limiter

# 预编译正则表达式
REPLY_USER_RE = re.compile(r'\[([^\]]+)\]\s*\(([^\)]+)\)')
TIME_RE = re.compile(r'\(\d{4}-\d{2}-\d{2}[\s\d:]+\)')
CLEAN_IMG_RE = re.compile(r'^显示图片\(\d+K\)')


class DiscordSender:
    def __init__(self, webhook_url):
        self.webhook_url = webhook_url
        self._limiter = get_discord_limiter()
    
    async def send_reply(self, reply):
        """
        发送回复到 Discord webhook (异步，带限流)
        
        Args:
            reply: 回复数据字典（需包含分离的 quote_content 和 main_content）
            
        Returns:
            bool: 是否发送成功
        """
        # 等待限流许可
        can_send = await self._limiter.acquire(timeout=30)
        if not can_send:
            print(f"[DiscordSender] 限流等待超时，跳过发送")
            return False
        
        try:
            quote_content = reply.get('quote_content', '')
            main_content = reply.get('main_content', '')
            
            # 从 quote_content 提取回复对象信息
            reply_to_match = REPLY_USER_RE.search(quote_content)
            if reply_to_match:
                reply_to_user = reply_to_match.group(1)
                reply_to_time = reply_to_match.group(2)
            else:
                reply_to_user = None
                reply_to_time = None
            
            # 清理引用内容（去掉 +R by [...] (时间) 开头）
            if quote_content:
                # 移除 +R by [用户名] (时间) 前缀
                quote_content = re.sub(r'^\+R\s+by\s+\[[^\]]+\]\s*\([^)]+\)', '', quote_content).strip()
                time_match = TIME_RE.search(quote_content)
                if time_match:
                    quote_content = quote_content[time_match.end():].strip()
                else:
                    lines = quote_content.split('\n')
                    if len(lines) > 1:
                        quote_content = '\n'.join(lines[1:]).strip()
            
            # 清理主内容
            main_content = CLEAN_IMG_RE.sub('', main_content).strip()
            
            # 构建 URL，添加 page=9999
            url = reply.get('url', '')
            if 'tid=' in url:
                url += '&page=9999' if '?' in url else '?page=9999'
            
            target_name = reply.get('target_name', '')
            topic_title = reply.get('topic_title', '未知主题')
            
            # 主内容处理 - 限制长度并清理
            main_text = main_content[:900] if main_content else "无内容"
            
            # 构建 Discord embed
            embed = {
                "title": f"💬 {target_name[:250]}" if target_name else f"💬 {topic_title[:250]}",
                "url": url,
                "color": 0xe74c3c,
                "fields": [],
                "footer": {
                    "text": f"TID: {reply.get('tid', 'N/A')} | PID: {reply.get('pid', 'N/A')}"
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # 字段1: 主题 + 引用（引用放在主题下方）
            topic_field_parts = []
            topic_field_parts.append(f"📌 **主题**\n{topic_title[:200]}")
            
            # 如果有引用内容，放在主题下方
            if quote_content:
                quote_text = quote_content[:250]
                if len(quote_content) > 250:
                    quote_text += "..."
                topic_field_parts.append(f"💬 **引用**\n> {quote_text}")
            
            # 如果有回复对象信息
            if reply_to_user:
                reply_info = f"👤 **回复对象**: {reply_to_user}"
                if reply_to_time:
                    reply_info += f" ({reply_to_time})"
                topic_field_parts.append(reply_info)
            
            embed["fields"].append({
                "name": "─────────────────────────────",
                "value": "\n\n".join(topic_field_parts)[:1024],
                "inline": False
            })
            
            # 字段2: 正文回复（放在主题/引用之后）
            embed["fields"].append({
                "name": "📝 正文回复",
                "value": f"```{main_text[:1000]}```"[:1024],
                "inline": False
            })
            
            # 图片
            images = reply.get('images', [])
            if images:
                embed["image"] = {"url": images[0]}
                if len(images) > 1:
                    image_list = "\n".join([f"[{i+1}] {url}" for i, url in enumerate(images[1:5])])
                    if len(images) > 5:
                        image_list += f"\n... 还有 {len(images) - 5} 张图片"
                    embed["fields"].append({
                        "name": f"🖼️ 其他图片 ({len(images)-1} 张)",
                        "value": image_list[:1024],
                        "inline": False
                    })
            
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    self.webhook_url,
                    json={"embeds": [embed]},
                    headers={'Content-Type': 'application/json'}
                )
            
            return response.status_code == 204
            
        except httpx.TimeoutException:
            print(f"发送 Discord webhook 超时")
            return False
        except httpx.RequestError as e:
            print(f"发送 Discord webhook 请求失败: {e}")
            return False
        except Exception as e:
            print(f"发送 Discord webhook 失败: {e}")
            return False
