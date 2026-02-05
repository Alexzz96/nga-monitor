#!/usr/bin/env python3
"""
NGA 爬虫模块（异步版本）
"""

import re
import json
from datetime import datetime, timezone
from playwright.async_api import async_playwright

class NgaCrawler:
    def __init__(self, storage_state_path):
        self.storage_state_path = storage_state_path
    
    async def fetch_replies(self, target_url):
        """
        异步抓取指定 URL 的用户回复
        
        Returns:
            list: 回复列表
        """
        with open(self.storage_state_path, "r") as f:
            state = json.load(f)
        
        replies = []
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=state)
            page = await context.new_page()
            
            try:
                await page.goto(target_url, wait_until="networkidle", timeout=30000)
                
                html = await page.content()
                if "ERROR:2048" in html or "必须登录" in html:
                    raise Exception("NGA 登录失效，请重新导出 storage state")
                
                await page.wait_for_timeout(5000)
                
                rows = await page.locator("tr.topicrow").all()
                
                for row in rows:
                    try:
                        reply = await self._extract_reply(row)
                        if reply:
                            replies.append(reply)
                    except Exception:
                        continue
                
                await context.storage_state(path=self.storage_state_path)
                
            finally:
                await browser.close()
        
        return replies
    
    async def _extract_reply(self, row):
        """从行元素提取回复数据"""
        # 回复数
        replies_count = ""
        try:
            replies_count = await row.locator("td.c1 a.replies").inner_text()
        except:
            pass
        
        # 主题标题和链接
        topic_link = row.locator("td.c2 a.topic").first
        topic_title = await topic_link.inner_text()
        topic_href = await topic_link.get_attribute("href")
        
        tid_match = re.search(r"tid=(\d+)", topic_href)
        tid = tid_match.group(1) if tid_match else ""
        
        # 提取内容和图片
        quote_content = ""
        main_content = ""
        images = []
        
        try:
            postcontent_elem = row.locator("td.c2 .postcontent").first
            postcontent_html = await postcontent_elem.inner_html()
            
            quote_start = postcontent_html.find('<div class="quote">')
            
            if quote_start != -1:
                quote_end = postcontent_html.find('</div>', quote_start)
                
                if quote_end != -1:
                    quote_html = postcontent_html[quote_start:quote_end + 6]
                    quote_content = re.sub(r'<[^>]+>', '', quote_html)
                    quote_content = re.sub(r'\n+', '\n', quote_content).strip()
                    
                    main_html = postcontent_html[quote_end + 6:]
                    main_html = re.sub(r'^(\s*<br\s*/?>\s*)+', '', main_html)
                    main_content = re.sub(r'<[^>]+>', '', main_html)
                    main_content = re.sub(r'\n+', '\n', main_content).strip()
            else:
                main_content = re.sub(r'<[^>]+>', '', postcontent_html)
                main_content = re.sub(r'\n+', '\n', main_content).strip()
            
            # 提取图片
            img_pattern = r'<img[^\u003e]*data-srcorg="([^"]+)"[^\u003e]*\u003e'
            img_matches = re.findall(img_pattern, postcontent_html)
            if img_matches:
                images = img_matches
            else:
                img_pattern2 = r'<img[^\u003e]*src="(https?://[^"]+)"[^\u003e]*\u003e'
                img_matches2 = re.findall(img_pattern2, postcontent_html)
                images = [img for img in img_matches2 if 'about:blank' not in img]
                
        except:
            pass
        
        # 时间
        post_date = ""
        post_datetime = None
        try:
            date_elem = row.locator("td.c3 .postdate").first
            post_date = await date_elem.inner_text()
            
            title_attr = await date_elem.get_attribute("title")
            if title_attr:
                post_date = title_attr
            
            for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
                try:
                    post_datetime = datetime.strptime(post_date, fmt)
                    break
                except:
                    continue
        except:
            pass
        
        # 版块
        forum = ""
        try:
            forum = await row.locator("td.c2 .titleadd2 a").inner_text()
        except:
            pass
        
        # pid
        pid = ""
        try:
            postcontent = row.locator("td.c2 .postcontent span").first
            pid_attr = await postcontent.get_attribute("id")
            if pid_attr:
                pid_match = re.search(r"(\d+)$", pid_attr)
                if pid_match:
                    pid = pid_match.group(1)
        except:
            pass
        
        # URL
        reply_url = f"https://nga.178.com/read.php?tid={tid}"
        if pid:
            reply_url += f"#pid{pid}"
        
        pid_numeric = int(pid) if pid and pid.isdigit() else 0
        post_timestamp = post_datetime.timestamp() if post_datetime else pid_numeric
        
        # 组合完整内容（兼容旧代码）
        content_full = f"[引用]\n{quote_content}\n\n[回复]\n{main_content}" if quote_content else main_content
        
        return {
            "tid": tid,
            "pid": pid,
            "pid_numeric": pid_numeric,
            "topic_title": topic_title[:200],
            "quote_content": quote_content,
            "main_content": main_content,
            "content_full": content_full,
            "images": images,
            "forum": forum,
            "post_date": post_date,
            "post_timestamp": post_timestamp,
            "replies_count": replies_count,
            "url": reply_url,
            "scraped_at": datetime.now(timezone.utc).isoformat()
        }
