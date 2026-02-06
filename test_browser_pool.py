#!/usr/bin/env python3
"""
浏览器复用性能测试脚本
对比新旧方案的内存占用和启动时间
"""

import asyncio
import time
import psutil
import os
from datetime import datetime

# 测试配置
STORAGE_STATE_PATH = "/app/data/storage_state.json"
TEST_URL = "https://nga.178.com/thread.php?searchpost=1&authorid=557398"


def get_memory_mb():
    """获取当前进程内存占用 (MB)"""
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / 1024 / 1024


async def test_old_approach():
    """测试旧方案：每次启动新浏览器"""
    print("\n" + "="*60)
    print("测试旧方案：每次启动新浏览器")
    print("="*60)
    
    from playwright.async_api import async_playwright
    import json
    
    start_mem = get_memory_mb()
    start_time = time.time()
    
    with open(STORAGE_STATE_PATH, "r") as f:
        state = json.load(f)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=state)
        page = await context.new_page()
        
        await page.goto(TEST_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
        
        rows = await page.locator("tr.topicrow").all()
        row_count = len(rows)
        
        await browser.close()
    
    elapsed = time.time() - start_time
    end_mem = get_memory_mb()
    
    print(f"启动时间: {elapsed:.2f}s")
    print(f"内存占用: {start_mem:.1f}MB → {end_mem:.1f}MB (增加 {end_mem-start_mem:.1f}MB)")
    print(f"抓取行数: {row_count}")
    
    return {
        "approach": "old",
        "time": elapsed,
        "memory_delta": end_mem - start_mem,
        "rows": row_count
    }


async def test_new_approach():
    """测试新方案：使用浏览器连接池"""
    print("\n" + "="*60)
    print("测试新方案：使用浏览器连接池")
    print("="*60)
    
    from browser_pool import ManagedBrowserContext
    
    start_mem = get_memory_mb()
    start_time = time.time()
    
    async with ManagedBrowserContext(STORAGE_STATE_PATH, save_state_on_exit=True) as context:
        page = await context.new_page()
        
        await page.goto(TEST_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
        
        rows = await page.locator("tr.topicrow").all()
        row_count = len(rows)
        
        await page.close()
    
    elapsed = time.time() - start_time
    end_mem = get_memory_mb()
    
    print(f"启动时间: {elapsed:.2f}s")
    print(f"内存占用: {start_mem:.1f}MB → {end_mem:.1f}MB (增加 {end_mem-start_mem:.1f}MB)")
    print(f"抓取行数: {row_count}")
    
    return {
        "approach": "new",
        "time": elapsed,
        "memory_delta": end_mem - start_mem,
        "rows": row_count
    }


async def test_reuse():
    """测试连接池复用效果：连续抓取多次"""
    print("\n" + "="*60)
    print("测试连接池复用效果：连续抓取3次")
    print("="*60)
    
    from browser_pool import ManagedBrowserContext
    
    times = []
    mems = []
    
    for i in range(3):
        start_mem = get_memory_mb()
        start_time = time.time()
        
        async with ManagedBrowserContext(STORAGE_STATE_PATH, save_state_on_exit=True) as context:
            page = await context.new_page()
            
            await page.goto(TEST_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1500)
            
            rows = await page.locator("tr.topicrow").all()
            
            await page.close()
        
        elapsed = time.time() - start_time
        end_mem = get_memory_mb()
        
        times.append(elapsed)
        mems.append(end_mem - start_mem)
        
        print(f"  第{i+1}次: {elapsed:.2f}s, 内存+{end_mem-start_mem:.1f}MB, {len(rows)}行")
    
    print(f"\n平均时间: {sum(times)/len(times):.2f}s")
    print(f"平均内存增量: {sum(mems)/len(mems):.1f}MB")
    print(f"第1次 vs 第3次: 时间减少 {((times[0]-times[2])/times[0]*100):.0f}%")


async def cleanup():
    """清理浏览器池"""
    from browser_pool import close_browser_pool
    await close_browser_pool()


async def main():
    """主测试函数"""
    print("\n" + "="*60)
    print("NGA Monitor 浏览器复用性能测试")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    # 检查 storage state 文件
    if not os.path.exists(STORAGE_STATE_PATH):
        print(f"\n错误: Storage state 文件不存在: {STORAGE_STATE_PATH}")
        print("请先运行 export_nga_state.py 生成登录状态")
        return
    
    results = []
    
    try:
        # 测试新方案（先测试，避免旧方案占用内存影响）
        result_new = await test_new_approach()
        results.append(result_new)
        
        # 等待内存释放
        await asyncio.sleep(2)
        
        # 测试复用效果
        await test_reuse()
        
        # 清理后测试旧方案
        await cleanup()
        await asyncio.sleep(2)
        
        result_old = await test_old_approach()
        results.append(result_old)
        
    finally:
        await cleanup()
    
    # 对比结果
    print("\n" + "="*60)
    print("对比总结")
    print("="*60)
    
    old = next(r for r in results if r["approach"] == "old")
    new = next(r for r in results if r["approach"] == "new")
    
    print(f"启动时间: 旧方案 {old['time']:.2f}s → 新方案 {new['time']:.2f}s (快 {((old['time']-new['time'])/old['time']*100):.0f}%)")
    print(f"内存占用: 旧方案 +{old['memory_delta']:.1f}MB → 新方案 +{new['memory_delta']:.1f}MB (省 {(old['memory_delta']-new['memory_delta']):.1f}MB)")
    
    if new['time'] < old['time']:
        print("\n✅ 优化成功: 启动时间减少，内存占用减少")
    else:
        print("\n⚠️ 首次启动可能较慢，但后续请求会更快（复用效果）")


if __name__ == "__main__":
    asyncio.run(main())
