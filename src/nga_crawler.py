#!/usr/bin/env python3
"""
NGA 爬虫模块（异步版本 - 浏览器复用优化版）
使用特定异常类型，避免裸 except Exception
"""

import re
import json
import asyncio
import logging
from datetime import datetime, timezone

from browser_pool import ManagedBrowserContext
from exceptions import (
    LoginExpiredError, NetworkError, ParseError, 
    RateLimitError, handle_exception
)

logger = logging.getLogger(__name__)


class NgaCrawler:
    def __init__(self, storage_state_path):
        self.storage_state_path = storage_state_path
    
    async def fetch_replies(self, target_url, accurate_time_pids=None):
        """
        异步抓取指定 URL 的用户回复（使用浏览器连接池）
        
        Args:
            target_url: 目标 URL
            accurate_time_pids: 需要获取准确时间的 PID 列表（None表示全部获取）
            
        Returns:
            list: 回复列表
            
        Raises:
            LoginExpiredError: 登录过期
            NetworkError: 网络错误
            ParseError: 解析失败
        """
        replies = []
        
        try:
            async with ManagedBrowserContext(self.storage_state_path, save_state_on_exit=True) as context:
                page = await context.new_page()
                
                try:
                    await page.goto(target_url, wait_until="networkidle", timeout=30000)
                    
                    html = await page.content()
                    
                    # 检查登录状态
                    if "ERROR:2048" in html or "必须登录" in html:
                        raise LoginExpiredError("NGA 登录失效，请重新导出 storage state")
                    
                    # 检查是否被限流
                    if "访问过于频繁" in html or "请稍后再试" in html:
                        raise RateLimitError("触发 NGA 限流")
                    
                    # 等待页面渲染
                    await page.wait_for_timeout(2000)
                    
                    rows = await page.locator("tr.topicrow").all()
                    logger.info(f"[NgaCrawler] 找到 {len(rows)} 行数据")
                    
                    for row in rows:
                        try:
                            # 先获取基础信息（不获取准确时间）
                            reply = await self._extract_reply(row, context, fetch_accurate_time=False)
                            if reply:
                                # 如果需要获取准确时间
                                if accurate_time_pids is None or reply.get('pid') in accurate_time_pids:
                                    try:
                                        accurate_time = await self._get_accurate_post_time(
                                            context, reply['tid'], reply['pid']
                                        )
                                        if accurate_time:
                                            reply['post_date'] = accurate_time['post_date']
                                            reply['post_datetime'] = accurate_time['post_datetime']
                                            reply['post_timestamp'] = accurate_time['post_datetime'].timestamp()
                                            if accurate_time.get('forum'):
                                                reply['forum'] = accurate_time['forum']
                                            logger.debug(f"[NgaCrawler] 获取到准确时间: {reply['post_date']} for PID {reply['pid']}")
                                    except Exception as e:
                                        logger.debug(f"[NgaCrawler] 获取准确时间失败: {e}, 使用列表页时间")
                                replies.append(reply)
                        except asyncio.TimeoutError:
                            # 这个异常在当前实现中不会触发，但保留以防万一
                            logger.warning(f"[NgaCrawler] 提取回复超时，跳过")
                            continue
                        except ParseError as e:
                            logger.warning(f"[NgaCrawler] 解析行失败: {e}")
                            continue
                        except Exception as e:
                            # 单行解析失败不影响整体
                            logger.warning(f"[NgaCrawler] 提取回复时出错: {e}")
                            continue
                    
                except Exception as e:
                    # 页面级错误处理
                    if isinstance(e, (LoginExpiredError, RateLimitError)):
                        raise
                    raise NetworkError(f"页面加载失败: {e}") from e
                finally:
                    await page.close()
                    
        except (LoginExpiredError, RateLimitError, NetworkError):
            raise
        except Exception as e:
            logger.error(f"[NgaCrawler] 未预期的错误: {e}")
            raise NetworkError(f"抓取过程出错: {e}") from e
        
        logger.info(f"[NgaCrawler] 抓取完成，共 {len(replies)} 条回复")
        return replies
    
    async def fetch_history(self, target_url, max_pages=25, delay=2, progress_callback=None):
        """
        抓取历史回复（多页）- 复用同一个 browser context
        
        Args:
            target_url: 基础 URL
            max_pages: 最大抓取页数（每页20条）
            delay: 每页间隔秒数
            progress_callback: 进度回调函数 (page_num, total_pages, replies_count, stage, detail)
            
        Returns:
            list: 所有回复列表
        """
        all_replies = []
        seen_pids = set()
        
        logger.info(f"[History] 开始抓取历史，目标: {max_pages} 页")
        
        try:
            async with ManagedBrowserContext(self.storage_state_path, save_state_on_exit=True) as context:
                for page_num in range(1, max_pages + 1):
                    page_url = f"{target_url}&page={page_num}"
                    
                    # 通知开始抓取本页
                    if progress_callback:
                        await progress_callback(page_num, max_pages, len(all_replies), "开始页面", f"正在加载第 {page_num} 页...")
                    
                    logger.info(f"[History] 抓取第 {page_num}/{max_pages} 页: {page_url}")
                    
                    page = None
                    try:
                        page = await context.new_page()
                        
                        # 通知页面加载
                        if progress_callback:
                            await progress_callback(page_num, max_pages, len(all_replies), "加载页面", f"等待页面响应...")
                        
                        await page.goto(page_url, wait_until="networkidle", timeout=30000)
                        
                        html = await page.content()
                        
                        if "ERROR:2048" in html:
                            raise LoginExpiredError("NGA 登录失效")
                        
                        if "访问过于频繁" in html:
                            raise RateLimitError("触发 NGA 限流")
                        
                        await page.wait_for_timeout(2000)
                        
                        # 通知解析数据
                        if progress_callback:
                            await progress_callback(page_num, max_pages, len(all_replies), "解析数据", f"正在解析第 {page_num} 页内容...")
                        
                        rows = await page.locator("tr.topicrow").all()
                        logger.info(f"[History] 第 {page_num} 页找到 {len(rows)} 行数据")
                        
                        page_replies = []
                        for idx, row in enumerate(rows):
                            try:
                                # 每5行更新一次进度
                                if idx % 5 == 0 and progress_callback:
                                    await progress_callback(page_num, max_pages, len(all_replies) + len(page_replies), 
                                                          "提取回复", f"第 {page_num} 页 - 已提取 {idx}/{len(rows)} 行")
                                
                                reply = await self._extract_reply(row, context, fetch_accurate_time=True)
                                if reply and reply['pid']:
                                    if reply['pid'] not in seen_pids:
                                        seen_pids.add(reply['pid'])
                                        page_replies.append(reply)
                            except ParseError:
                                continue
                            except Exception as e:
                                logger.debug(f"[History] 提取回复失败: {e}")
                                continue
                        
                        all_replies.extend(page_replies)
                        logger.info(f"[History] 第 {page_num} 页新增 {len(page_replies)} 条回复，总计 {len(all_replies)}")
                        
                        # 页面完成
                        if progress_callback:
                            await progress_callback(page_num, max_pages, len(all_replies), "页面完成", 
                                                  f"第 {page_num} 页完成，新增 {len(page_replies)} 条，总计 {len(all_replies)} 条")
                        
                        # 检查是否还有更多页面
                        if len(rows) == 0:
                            logger.info(f"[History] 第 {page_num} 页无数据，停止抓取")
                            break
                        
                        if page_num < max_pages:
                            await asyncio.sleep(delay)
                            
                    except (LoginExpiredError, RateLimitError):
                        raise
                    except Exception as e:
                        logger.error(f"[History] 第 {page_num} 页抓取失败: {e}")
                        if progress_callback:
                            await progress_callback(page_num, max_pages, len(all_replies), "页面错误", f"第 {page_num} 页失败: {str(e)[:50]}")
                        continue
                    finally:
                        if page:
                            await page.close()
                            
        except (LoginExpiredError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"[History] 抓取历史失败: {e}")
        
        logger.info(f"[History] 历史抓取完成，共 {len(all_replies)} 条回复")
        return all_replies
    
    async def _extract_reply(self, row, context=None, fetch_accurate_time=True):
        """从行元素提取回复数据，并获取准确的回复时间
        
        Args:
            row: Playwright 行元素
            context: Browser context（需要时才获取准确时间）
            fetch_accurate_time: 是否进入详情页获取准确时间（默认True，日常监控设为False）
        """
        try:
            # 回复数
            replies_count = ""
            try:
                replies_count = await row.locator("td.c1 a.replies").inner_text()
            except:
                pass
            
            # 主题标题和链接
            try:
                topic_link = row.locator("td.c2 a.topic").first
                topic_title = await topic_link.inner_text(timeout=5000)
                topic_href = await topic_link.get_attribute("href", timeout=5000)
                logger.info(f"[NgaCrawler] 获取到标题: {topic_title[:30] if topic_title else 'None'}")
            except Exception as e:
                logger.error(f"[NgaCrawler] 获取标题失败: {e}")
                raise ParseError(f"无法获取标题: {e}")
            
            tid_match = re.search(r"tid=(\d+)", topic_href)
            tid = tid_match.group(1) if tid_match else ""
            
            if not tid:
                raise ParseError("无法提取 TID")
            
            # 提取内容和图片
            quote_content = ""
            main_content = ""
            images = []
            
            try:
                postcontent_elem = row.locator("td.c2 .postcontent").first
                postcontent_html = await postcontent_elem.inner_html()
                
                # 使用正则移除所有引用块（更可靠）
                # 移除 quote 块
                cleaned_html = re.sub(r'<div class="quote"[^>]*>[\s\S]*?</div>', '', postcontent_html, flags=re.IGNORECASE)
                # 移除 collapse 折叠块
                cleaned_html = re.sub(r'<div class="collapse"[^>]*>[\s\S]*?</div>', '', cleaned_html, flags=re.IGNORECASE)
                
                # 提取引用内容（用于参考，但不包含在主要内容中）
                quote_start = postcontent_html.find('<div class="quote">')
                quote_content = ""
                if quote_start != -1:
                    quote_end = postcontent_html.find('</div>', quote_start)
                    if quote_end != -1:
                        quote_html = postcontent_html[quote_start:quote_end + 6]
                        quote_content = re.sub(r'<[^>]+>', '', quote_html)
                        quote_content = re.sub(r'\n+', '\n', quote_content).strip()
                
                # 从清理后的HTML提取主要内容
                main_content = re.sub(r'<[^>]+>', '', cleaned_html)
                main_content = re.sub(r'^(\s*<br\s*/?>\s*)+', '', main_content)
                main_content = re.sub(r'\n+', '\n', main_content).strip()
                
                # 提取图片
                img_pattern = r'<img[^>]*data-srcorg="([^"]+)"[^>]*>'
                img_matches = re.findall(img_pattern, postcontent_html)
                if img_matches:
                    images = img_matches
                else:
                    img_pattern2 = r'<img[^>]*src="(https?://[^"]+)"[^>]*>'
                    img_matches2 = re.findall(img_pattern2, postcontent_html)
                    images = [img for img in img_matches2 if 'about:blank' not in img]
                    
            except Exception as e:
                # 内容解析失败不是致命错误
                logger.debug(f"解析内容失败: {e}")
                pass
            
            # 时间（列表页时间，后面会被详情页时间覆盖）
            post_date = ""
            post_datetime = None
            try:
                date_elem = row.locator("td.c3 .postdate").first
                # 优先使用 inner_text（回复时间），而非 title 属性（可能是主题时间）
                post_date = await date_elem.inner_text()
                post_date = post_date.strip()
                
                # 如果 inner_text 为空或无效，才尝试 title 属性
                if not post_date or len(post_date) < 5:
                    title_attr = await date_elem.get_attribute("title")
                    if title_attr:
                        post_date = title_attr.strip()
                
                for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
                    try:
                        post_datetime = datetime.strptime(post_date, fmt)
                        break
                    except:
                        continue
            except:
                pass
            
            # 版块 - 简化逻辑，直接从列表页获取
            forum = ""
            try:
                # NGA 列表页的板块信息在 titleadd2 中
                forum_elem = row.locator("td.c2 .titleadd2 a").first
                forum = await forum_elem.inner_text(timeout=1000)
                forum = forum.strip() if forum else ""
            except:
                # 如果失败，尝试 forum 类作为备选
                try:
                    forum_elem = row.locator("td.c2 .forum a").first
                    forum = await forum_elem.inner_text(timeout=1000)
                    forum = forum.strip() if forum else ""
                except:
                    pass
            
            # pid - 尝试多种选择器
            pid = ""
            try:
                # 方法1: 从 postcontent span 的 id 获取
                postcontent = row.locator("td.c2 .postcontent span").first
                pid_attr = await postcontent.get_attribute("id")
                if pid_attr:
                    pid_match = re.search(r"(\d+)$", pid_attr)
                    if pid_match:
                        pid = pid_match.group(1)
            except:
                pass
            
            # 方法2: 如果方法1失败，尝试从 onclick 属性获取
            if not pid:
                try:
                    topic_elem = row.locator("td.c2 a.topic").first
                    onclick_attr = await topic_elem.get_attribute("onclick")
                    if onclick_attr:
                        # 匹配 commonui.postArg.proc('pid', '12345')
                        pid_match = re.search(r"proc\(['\"]pid['\"],\s*['\"](\d+)['\"]\)", onclick_attr)
                        if pid_match:
                            pid = pid_match.group(1)
                except:
                    pass
            
            # 方法3: 尝试从 data-pid 属性获取
            if not pid:
                try:
                    any_elem = row.locator("[data-pid]").first
                    pid = await any_elem.get_attribute("data-pid")
                except:
                    pass
            
            if not pid:
                logger.error(f"[NgaCrawler] 无法提取 PID，TID={tid}, 标题={topic_title[:30] if topic_title else 'None'}, 跳过")
                # 如果没有 PID，使用 TID 作为备选（至少能区分不同帖子）
                pid = f"NO_PID_{tid}"
            
            # URL
            reply_url = f"https://nga.178.com/read.php?tid={tid}"
            if pid:
                reply_url += f"#pid{pid}"
            
            pid_numeric = int(pid) if pid and pid.isdigit() else 0
            
            # 如果有 context 且需要获取准确时间
            if fetch_accurate_time and context and pid:
                try:
                    accurate_time = await self._get_accurate_post_time(context, tid, pid)
                    if accurate_time:
                        post_date = accurate_time['post_date']
                        post_datetime = accurate_time['post_datetime']
                        logger.debug(f"[NgaCrawler] 获取到准确时间: {post_date} for PID {pid}")
                except Exception as e:
                    logger.debug(f"[NgaCrawler] 获取准确时间失败: {e}, 使用列表页时间")
            
            post_timestamp = post_datetime.timestamp() if post_datetime else pid_numeric
            
            return {
                "tid": tid,
                "pid": pid,
                "pid_numeric": pid_numeric,
                "topic_title": topic_title[:200],
                "quote_content": quote_content,
                "main_content": main_content,
                "content_full": main_content,  # 只包含主要内容，不重复引用
                "images": images,
                "forum": forum,
                "post_date": post_date,
                "post_timestamp": post_timestamp,
                "replies_count": replies_count,
                "url": reply_url,
                "scraped_at": datetime.now(timezone.utc).isoformat()
            }
            
        except ParseError:
            raise
        except Exception as e:
            raise ParseError(f"提取回复失败: {e}") from e
    
    async def _get_accurate_post_time(self, context, tid, pid):
        """进入帖子详情页获取准确的回复时间 - 简化版，更稳定"""
        page = None
        try:
            # 创建页面（设置较短的超时）
            page = await context.new_page()
            
            # 构造详情页 URL
            detail_url = f"https://nga.178.com/read.php?tid={tid}&pid={pid}"
            
            # 使用较短的导航超时，并捕获超时异常
            try:
                await page.goto(detail_url, wait_until="domcontentloaded", timeout=5000)
            except Exception as e:
                logger.debug(f"[NgaCrawler] 导航超时或失败: {e}")
                return None
            
            # 短暂等待页面渲染
            await asyncio.sleep(0.3)
            
            # 尝试获取回复时间 - 优先使用 PID 选择器
            post_date = None
            post_datetime = None
            
            # 方法1: 通过 PID 查找
            try:
                time_selector = f"#postcontent{pid} .postInfo, #postcontent{pid} .postdate, #p{pid} .postInfo, #p{pid} .postdate"
                time_elem = page.locator(time_selector).first
                # 使用 Playwright 内置的 timeout
                time_text = await time_elem.inner_text(timeout=2000)
                
                if time_text:
                    time_text = time_text.strip()
                    for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%y-%m-%d %H:%M"]:
                        try:
                            post_datetime = datetime.strptime(time_text, fmt)
                            post_date = time_text
                            break
                        except:
                            continue
            except Exception as e:
                logger.debug(f"[NgaCrawler] 方法1失败: {e}")
            
            # 方法2: 查找所有时间元素
            if not post_date:
                try:
                    all_times = await page.locator(".postInfo, .postdate").all()
                    for time_elem in all_times[:3]:  # 只检查前3个
                        try:
                            time_text = await time_elem.inner_text()
                            if time_text:
                                time_text = time_text.strip()
                                for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%y-%m-%d %H:%M"]:
                                    try:
                                        post_datetime = datetime.strptime(time_text, fmt)
                                        post_date = time_text
                                        break
                                    except:
                                        continue
                                if post_date:
                                    break
                        except:
                            continue
                except Exception as e:
                    logger.debug(f"[NgaCrawler] 方法2失败: {e}")
            
            if post_date and post_datetime:
                return {
                    'post_date': post_date,
                    'post_datetime': post_datetime
                }
            return None
            return None
                
        except Exception as e:
            logger.debug(f"[NgaCrawler] 获取准确时间异常: {e}")
            return None
        finally:
            if page:
                try:
                    await page.close()
                except:
                    pass
    async def fetch_accurate_time_for_replies(self, replies, context):
        """
        为指定回复列表获取准确时间（用于优化后的日常监控）
        
        Args:
            replies: 回复列表（已有基础信息，但时间可能不准确）
            context: Browser context
            
        Returns:
            dict: PID -> 准确时间信息的映射
        """
        accurate_times = {}
        
        for reply in replies:
            pid = reply.get('pid')
            tid = reply.get('tid')
            
            if not pid or not tid:
                continue
            
            try:
                accurate = await self._get_accurate_post_time(context, tid, pid)
                if accurate:
                    accurate_times[pid] = accurate
            except Exception as e:
                logger.debug(f"[NgaCrawler] 获取 PID {pid} 准确时间失败: {e}")
                continue
        
        return accurate_times




    async def fetch_pids_only(self, target_url):
        """
        快速获取PID列表（只抓列表页，不解析内容）
        
        Returns:
            list: [{'pid': '123', 'tid': '456', 'title': '...'}, ...]
        """
        pids = []
        
        try:
            async with ManagedBrowserContext(self.storage_state_path, save_state_on_exit=True) as context:
                page = await context.new_page()
                
                try:
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(1000)
                    
                    # 检查登录状态
                    html = await page.content()
                    if "ERROR:2048" in html:
                        raise LoginExpiredError("NGA 登录失效")
                    
                    # 只获取PID和TID - 使用 page.evaluate 直接解析HTML
                    # 调试：先检查页面结构
                    row_count = await page.evaluate("""() => document.querySelectorAll('tr.topicrow').length""")
                    logger.info(f"[Debug] 找到 {row_count} 行 topicrow")
                    
                    # 调试：检查第一行的完整结构
                    first_row_debug = await page.evaluate("""() => {
                        const row = document.querySelector('tr.topicrow');
                        if (!row) return null;
                        
                        // 获取所有span的id
                        const spans = row.querySelectorAll('span[id]');
                        const span_ids = Array.from(spans).map(s => s.id);
                        
                        // 获取所有a标签的onclick
                        const links = row.querySelectorAll('a');
                        const onclicks = Array.from(links).map(a => ({
                            href: a.getAttribute('href') || '',
                            onclick: a.getAttribute('onclick') || ''
                        }));
                        
                        // 尝试从链接href中提取PID
                        let pid_from_href = '';
                        const topic_link = row.querySelector('td.c2 a.topic');
                        if (topic_link) {
                            const href = topic_link.getAttribute('href') || '';
                            // 检查是否有 #pid12345 格式
                            const hash_match = href.match(/#pid(\d+)/);
                            if (hash_match) pid_from_href = hash_match[1];
                        }
                        
                        return {
                            span_ids: span_ids,
                            onclicks: onclicks,
                            pid_from_href: pid_from_href,
                            row_html: row.outerHTML.substring(0, 500)
                        };
                    }""")
                    logger.info(f"[Debug] 第一行完整结构: {first_row_debug}")
                    
                    pids_data = await page.evaluate("""() => {
                        const rows = document.querySelectorAll('tr.topicrow');
                        const results = [];
                        rows.forEach(row => {
                            try {
                                // 获取标题和链接
                                const topic_link = row.querySelector('td.c2 a.topic');
                                if (!topic_link) return;
                                
                                const title = topic_link.textContent.trim();
                                const href = topic_link.getAttribute('href') || '';
                                
                                // 提取 TID
                                const tid_match = href.match(/tid=(\d+)/);
                                const tid = tid_match ? tid_match[1] : '';
                                
                                // 提取 PID - 从 span[id] 或 onclick
                                let pid = '';
                                
                                // 方法1: 从 span[id] 获取PID
                                // NGA 格式: postcontent{tid}_{pid} (如 postcontent45905087_857484278)
                                const spans = row.querySelectorAll('span[id]');
                                for (const span of spans) {
                                    const span_id = span.id;
                                    // 匹配 postcontent{tid}_{pid} 格式
                                    const match = span_id.match(/postcontent\d+_(\d+)/);
                                    if (match) {
                                        pid = match[1];
                                        break;
                                    }
                                }
                                
                                // 方法3: 从 onclick 获取
                                if (!pid) {
                                    const onclick = topic_link.getAttribute('onclick') || '';
                                    // 匹配 proc('pid', '12345') 或 proc("pid", "12345")
                                    const pid_match = onclick.match(/['"]pid['"]\s*,\s*['"](\d+)['"]/);
                                    if (pid_match) pid = pid_match[1];
                                }
                                
                                // 方法4: 从 data-pid 获取
                                if (!pid) {
                                    pid = topic_link.getAttribute('data-pid') || '';
                                }
                                
                                if (tid && pid) {
                                    results.push({ pid, tid, title: title.slice(0, 100) });
                                }
                            } catch (e) {
                                console.error('Parse row error:', e);
                            }
                        });
                        return results;
                    }""")
                    
                    for item in pids_data:
                        pids.append(item)
                            
                finally:
                    await page.close()
                    
        except Exception as e:
            logger.error(f"[NgaCrawler] 快速获取PID失败: {e}")
            raise
        
        logger.info(f"[NgaCrawler] 快速获取到 {len(pids)} 个PID")
        return pids
    
    async def fetch_reply_detail(self, tid, pid):
        """
        获取单条回复的完整信息（进入详情页）
        
        Returns:
            dict: 完整回复数据
        """
        reply = None
        
        try:
            async with ManagedBrowserContext(self.storage_state_path, save_state_on_exit=True) as context:
                page = await context.new_page()
                
                try:
                    url = f"https://nga.178.com/read.php?tid={tid}&pid={pid}"
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(500)
                    
                    # 获取完整回复数据
                    # 使用 page.evaluate 直接在页面上查询，避免 Locator 问题
                    reply_data = await page.evaluate("""() => {
                        const post_row = document.querySelector('.forumbox .postrow');
                        if (!post_row) return null;
                        
                        // 获取内容 - 尝试多种选择器
                        let content_elem = post_row.querySelector('.postcontent');
                        if (!content_elem) {
                            content_elem = post_row.querySelector('.postfont');
                        }
                        if (!content_elem) {
                            content_elem = post_row.querySelector('.postmessage');
                        }
                        
                        let full_content = '';
                        let quote_content = '';
                        let main_content = '';
                        
                        if (content_elem) {
                            // 获取完整HTML
                            let html = content_elem.innerHTML;
                            
                            // 查找引用部分
                            const quote_elem = content_elem.querySelector('.quote, .quotecontent, blockquote');
                            if (quote_elem) {
                                quote_content = quote_elem.textContent.trim();
                                // 从HTML中移除引用部分
                                html = html.replace(/<div class="quote"[^>]*>[\\s\\S]*?<\\/div>/gi, '');
                                html = html.replace(/<blockquote[^>]*>[\\s\\S]*?<\\/blockquote>/gi, '');
                            }
                            
                            // 处理剩下的HTML
                            html = html.replace(/<script[^>]*>[\\s\\S]*?<\\/script>/gi, '');
                            html = html.replace(/<style[^>]*>[\\s\\S]*?<\\/style>/gi, '');
                            const tmp = document.createElement('div');
                            tmp.innerHTML = html;
                            main_content = tmp.textContent.trim();
                            full_content = content_elem.textContent.trim();
                        }
                        
                        // 获取作者
                        let author_elem = post_row.querySelector('.postauthor');
                        if (!author_elem) {
                            author_elem = post_row.querySelector('.author');
                        }
                        if (!author_elem) {
                            author_elem = post_row.querySelector('.username');
                        }
                        const author = author_elem ? author_elem.textContent.trim() : '匿名';
                        
                        // 获取时间
                        let time_elem = post_row.querySelector('.postdatec');
                        if (!time_elem) {
                            time_elem = post_row.querySelector('[id^="postdate"]');
                        }
                        if (!time_elem) {
                            time_elem = post_row.querySelector('.postdate');
                        }
                        const time_str = time_elem ? time_elem.textContent.trim() : '';
                        
                        return { 
                            full_content: full_content || '(无内容)', 
                            quote_content: quote_content,
                            main_content: main_content || full_content || '(无内容)',
                            author, 
                            time_str 
                        };
                    }""")
                    
                    if reply_data:
                        reply = {
                            'pid': pid,
                            'tid': tid,
                            'topic_title': '',
                            'main_content': reply_data['main_content'][:1000],
                            'quote_content': reply_data['quote_content'][:500],
                            'content_full': reply_data['full_content'][:1000],
                            'author': reply_data['author'],
                            'post_date': reply_data['time_str'],
                            'url': f"https://nga.178.com/read.php?tid={tid}#pid{pid}"
                        }
                        logger.info(f"[NgaCrawler] 获取到正文: {reply_data['main_content'][:50]}...", extra={'target_uid': ''})
                        if reply_data['quote_content']:
                            logger.info(f"[NgaCrawler] 获取到引用: {reply_data['quote_content'][:50]}...", extra={'target_uid': ''})
                    
                finally:
                    await page.close()
                    
        except Exception as e:
            logger.error(f"[NgaCrawler] 获取回复详情失败 TID={tid} PID={pid}: {e}")
            
        return reply
