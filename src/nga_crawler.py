#!/usr/bin/env python3
"""
NGA 爬虫模块（异步版本）
"""

import re
import json
import asyncio
import logging
from datetime import datetime, timezone
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

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
    
    async def fetch_history(self, target_url, max_pages=25, delay=2, progress_callback=None):
        """
        抓取历史回复（多页）
        
        Args:
            target_url: 基础 URL
            max_pages: 最大抓取页数（每页20条）
            delay: 每页间隔秒数
            progress_callback: 进度回调函数 (page_num, total_pages, replies_count)
            
        Returns:
            list: 所有回复列表
        """
        import asyncio
        
        all_replies = []
        seen_pids = set()
        
        logger = __import__('logging').getLogger(__name__)
        logger.info(f"[History] 开始抓取历史，目标: {max_pages} 页")
        
        for page_num in range(1, max_pages + 1):
            # 构建分页 URL
            page_url = target_url
            if '?' in page_url:
                page_url += f'&page={page_num}'
            else:
                page_url += f'?page={page_num}'
            
            logger.info(f"[History] 正在抓取第 {page_num}/{max_pages} 页: {page_url}")
            
            try:
                replies = await self.fetch_replies(page_url)
                
                # 去重
                new_count = 0
                for reply in replies:
                    pid = reply.get('pid')
                    if pid and pid not in seen_pids:
                        seen_pids.add(pid)
                        all_replies.append(reply)
                        new_count += 1
                
                logger.info(f"[History] 第 {page_num} 页抓取完成: {len(replies)} 条，新增 {new_count} 条")
                
                # 调用进度回调
                if progress_callback:
                    await progress_callback(page_num, max_pages, len(all_replies))
                
                # 如果一页都没有新数据，可能已到末尾
                if len(replies) == 0:
                    logger.info(f"[History] 第 {page_num} 页无数据，停止抓取")
                    break
                
                # 延迟，避免被封
                if page_num < max_pages:
                    logger.debug(f"[History] 等待 {delay} 秒...")
                    await asyncio.sleep(delay)
                    
            except Exception as e:
                logger.error(f"[History] 第 {page_num} 页抓取失败: {e}")
                continue
        
        logger.info(f"[History] 历史抓取完成，共 {len(all_replies)} 条回复")
        return all_replies
    
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
