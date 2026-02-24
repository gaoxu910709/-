#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""" FoamGirl 中文站爬虫 | 分页修复版 | 确保遍历所有分页 """
import os
import re
import time
import random
import sys
import requests
import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urljoin, urlparse
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# -------- 日志配置 --------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("foamgirl_fix.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# -------- 核心配置 --------
BASE_URL = "https://foamgirl.net/chinese"
ALBUM_URL_PATTERN = r'https://foamgirl\.net/\d+\.html'
IS_MOBILE = os.path.exists("/sdcard/Download")

DEFAULT_SAVE_DIR_MOBILE = "/sdcard/Download/脚本爬取文件/foamgirl"
DEFAULT_SAVE_DIR_WIN = r"C:\爬取结果\FoamGirl_Chinese_Fix"
MIN_IMAGE_SIZE = 100 * 1024  # 过滤小于100KB的图片
COMPLETED_FLAG = ".album_completed"

class FoamGirlSpider:
    def __init__(self, save_path, verify=True):
        self.save_path = save_path
        self.verify = verify
        self.session = self._init_session()
        self.failed_images = []

    def _init_session(self):
        session = requests.Session()
        retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Linux; Android 13; SM-G998B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36"
        ]
        return session

    def _get_headers(self):
        return {
            "User-Agent": random.choice(self.user_agents),
            "Referer": "https://foamgirl.net/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive"
        }

    def _get_response(self, url, retries=3):
        for i in range(retries):
            try:
                response = self.session.get(
                    url,
                    headers=self._get_headers(),
                    timeout=20,
                    allow_redirects=True,
                    verify=False,
                    stream=True
                )
                response.raise_for_status()
                return response
            except Exception as e:
                if i < retries - 1:
                    delay = random.uniform(1, 2)
                    logger.warning(f"请求失败 {url} | 重试 {i+1}/{retries} | 延迟 {delay:.1f}s | 错误: {e}")
                    time.sleep(delay)
                else:
                    logger.error(f"请求彻底失败 {url} | 错误: {e}")
                    return None

    def _sanitize_filename(self, name):
        if not name:
            return "未知套图"
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", name)
        return safe_name[:80].strip()

    def _is_album_completed(self, album_dir):
        flag_path = os.path.join(album_dir, COMPLETED_FLAG)
        return os.path.exists(flag_path)

    def _mark_album_completed(self, album_dir):
        flag_path = os.path.join(album_dir, COMPLETED_FLAG)
        with open(flag_path, 'w', encoding='utf-8') as f:
            f.write(f"Completed at: {time.ctime()}")
        logger.info(f"📌 套图已标记为完成，下次启动将自动跳过")

    def _get_existing_images(self, album_dir):
        if not os.path.exists(album_dir):
            return set()
        existing = set()
        for fname in os.listdir(album_dir):
            if fname.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')) and not fname.startswith('.'):
                existing.add(fname)
        return existing

    def _parse_album_list_from_page(self, page_url):
        logger.info(f"\n[主页] 正在解析: {page_url}")
        response = self._get_response(page_url)
        if not response:
            return [], None

        soup = BeautifulSoup(response.text, 'html.parser')
        albums = []

        a_tags = soup.find_all('a', href=re.compile(ALBUM_URL_PATTERN))
        for a in a_tags:
            album_url = a['href']
            title = a.get('title', '') or a.get_text(strip=True)
            if not title:
                parent = a.find_parent(['div', 'article'])
                if parent:
                    title_tag = parent.find(['h2', 'h3', 'div', 'span'], class_=re.compile(r'title|name', re.IGNORECASE))
                    if title_tag:
                        title = title_tag.get_text(strip=True)
                if not title:
                    img = a.find('img')
                    if img:
                        title = img.get('alt', '') or f"套图_{len(albums)+1}"
            if not title:
                title = f"套图_{len(albums)+1}"
            title = self._sanitize_filename(title)
            albums.append( (title, album_url) )
            logger.info(f"✅ 发现套图: {title} | {album_url}")

        logger.info(f"[主页] 本页共 {len(albums)} 个套图")

        next_page_url = None
        nav = soup.find('nav', class_=re.compile(r'navigation|pagination', re.IGNORECASE))
        if nav:
            next_link = nav.find('a', string=re.compile(r'下一页|Next|»', re.IGNORECASE))
            if next_link and next_link.get('href'):
                next_page_url = next_link['href'] if next_link['href'].startswith('http') else urljoin(page_url, next_link['href'])
        if not next_page_url:
            current_page_num = 1
            match = re.search(r'/page/(\d+)', page_url)
            if match:
                current_page_num = int(match.group(1))
            next_page_num = current_page_num + 1
            next_page_candidate = f"{BASE_URL}/page/{next_page_num}/"
            if self._get_response(next_page_candidate):
                next_page_url = next_page_candidate

        return albums, next_page_url

    def _download_all_images_in_album(self, album_title, album_url):
        album_dir = os.path.join(self.save_path, album_title)
        
        if self._is_album_completed(album_dir):
            logger.info(f"⏭️ [套图跳过] {album_title} (已完成)")
            return True

        os.makedirs(album_dir, exist_ok=True)
        existing_images = self._get_existing_images(album_dir)
        if existing_images:
            logger.info(f"🔄 [套图续传] {album_title} (已存在 {len(existing_images)} 张，将补全剩余)")
        else:
            logger.info(f"\n🎬 [套图开始] {album_title}")

        all_img_urls = []
        current_page_url = album_url
        page_num = 1

        # --- 核心修复：强制遍历所有分页，直到找不到下一页为止 ---
        while current_page_url:
            logger.info(f"[套图详情] 正在解析第 {page_num} 页: {current_page_url}")
            response = self._get_response(current_page_url)
            if not response:
                break

            soup = BeautifulSoup(response.text, 'html.parser')
            img_urls = []

            content = soup.find('div', class_=re.compile(r'entry-content|content|main', re.IGNORECASE))
            if content:
                imgs = content.find_all('img')
                for img in imgs:
                    src = img.get('data-src', '') or img.get('src', '') or img.get('data-original', '')
                    if not src:
                        continue
                    if 'wp-content/uploads' in src and not any(x in src for x in ['avatar', 'icon', 'logo', 'thumbnail', 'favicon']):
                        high_res_src = re.sub(r'-\d+x\d+(\.\w+)$', r'\1', src)
                        img_urls.append(high_res_src)

            if not img_urls:
                imgs = soup.find_all('img')
                for img in imgs:
                    src = img.get('data-src', '') or img.get('src', '') or img.get('data-original', '')
                    if src.startswith('http') and 'wp-content/uploads' in src:
                        if not any(x in src for x in ['avatar', 'icon', 'logo', 'thumbnail']):
                            img_urls.append(src)

            all_img_urls.extend(img_urls)
            logger.info(f"[套图详情] 第 {page_num} 页提取到 {len(img_urls)} 张图片")

            # --- 修复：更全面地查找“下一页”链接 ---
            next_page_url = None
            # 1. 查找所有包含“>”或“下一页”的链接
            pagination = soup.find_all('a', href=True, string=re.compile(r'^>|^下一页|^Next', re.IGNORECASE))
            if pagination:
                next_page_url = pagination[-1]['href']  # 取最后一个，通常是下一页
            # 2. 如果没找到，查找所有页码链接，取最大的那个
            else:
                page_links = []
                for a in soup.find_all('a', href=True):
                    text = a.get_text(strip=True)
                    if text.isdigit():
                        page_links.append( (int(text), a['href']) )
                if page_links:
                    page_links.sort()
                    max_page, max_href = page_links[-1]
                    if max_page > page_num:
                        next_page_url = max_href

            # 处理URL
            if next_page_url:
                next_page_url = next_page_url if next_page_url.startswith('http') else urljoin(current_page_url, next_page_url)
                # 检查下一页是否有效
                if self._get_response(next_page_url):
                    current_page_url = next_page_url
                    page_num += 1
                    time.sleep(0.5)
                else:
                    logger.info(f"[套图详情] 下一页无效，已到达最后一页，共 {page_num} 页")
                    break
            else:
                logger.info(f"[套图详情] 未找到下一页，已到达最后一页，共 {page_num} 页")
                break

        all_img_urls = list(dict.fromkeys(all_img_urls))
        logger.info(f"[套图详情] 总共提取到 {len(all_img_urls)} 张高清图片")

        if not all_img_urls:
            logger.error(f"❌ 未提取到图片")
            return False

        success_count = 0
        fail_count = 0
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = []
            for img_idx, img_url in enumerate(all_img_urls, 1):
                img_ext = os.path.splitext(urlparse(img_url).path)[1] or '.jpg'
                img_name = f"{img_idx:03d}{img_ext}"
                
                if img_name in existing_images:
                    logger.debug(f"⏭️ 跳过已存在: {img_name}")
                    success_count += 1
                    continue

                img_path = os.path.join(album_dir, img_name)
                futures.append(executor.submit(self._download_single_image, img_url, img_path))

            for future in as_completed(futures):
                if future.result():
                    success_count += 1
                else:
                    fail_count += 1

        final_existing = len(self._get_existing_images(album_dir))
        if final_existing >= len(all_img_urls):
            self._mark_album_completed(album_dir)
            logger.info(f"✅ [套图完成] {album_title} (共{len(all_img_urls)}张)")
            return True
        else:
            logger.warning(f"⚠️ [套图未完成] {album_title} (已下载{final_existing}/{len(all_img_urls)}张，下次启动将续传)")
            return False

    def _download_single_image(self, img_url, save_path):
        if os.path.exists(save_path):
            if self._validate_image(save_path):
                logger.debug(f"[下载] 已存在，跳过: {os.path.basename(save_path)}")
                return True
            else:
                os.remove(save_path)

        response = self._get_response(img_url)
        if not response:
            self.failed_images.append((img_url, save_path))
            return False

        content_length = int(response.headers.get('Content-Length', 0))
        if content_length > 0 and content_length < MIN_IMAGE_SIZE:
            logger.error(f"[下载] 文件过小({content_length/1024:.1f}KB < 100KB): {img_url}")
            return False

        temp_path = save_path + ".tmp"
        try:
            with open(temp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=16384):
                    if chunk:
                        f.write(chunk)
            
            if self._validate_image(temp_path):
                os.rename(temp_path, save_path)
                logger.debug(f"📥 成功: {os.path.basename(save_path)}")
                return True
            else:
                os.remove(temp_path)
                self.failed_images.append((img_url, save_path))
                return False
        except Exception as e:
            logger.error(f"[下载] 写入失败: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            self.failed_images.append((img_url, save_path))
            return False

    def _validate_image(self, file_path):
        if os.path.getsize(file_path) < MIN_IMAGE_SIZE:
            return False
        if not self.verify:
            return True
        try:
            with Image.open(file_path) as img:
                img.verify()
            return True
        except Exception:
            return False

    def run(self):
        start_time = time.time()
        logger.info("="*70)
        logger.info(f"🚀 FoamGirl 爬虫 [分页修复版] 启动")
        logger.info(f"📂 保存路径: {self.save_path}")
        logger.info(f"🛡️  特性: 精准断点续传 | 过滤<100KB | 8线程极速下载 | 强制遍历分页")
        logger.info("="*70)

        current_page_url = BASE_URL
        total_albums = 0
        page_num = 1

        while current_page_url:
            logger.info(f"\n{'='*60}")
            logger.info(f"📖 正在处理主页第 {page_num} 页: {current_page_url}")
            logger.info(f"{'='*60}")

            albums, next_page_url = self._parse_album_list_from_page(current_page_url)
            if not albums:
                logger.info(f"[主页] 第 {page_num} 页无套图，停止爬取。")
                break

            total_albums += len(albums)
            for idx, (album_title, album_url) in enumerate(albums, 1):
                logger.info(f"\n🎬 [任务进度] {idx}/{len(albums)}")
                self._download_all_images_in_album(album_title, album_url)
                time.sleep(1)

            if next_page_url:
                logger.info(f"\n📖 主页第 {page_num} 页完成，翻页到下一页...")
                current_page_url = next_page_url
                page_num += 1
                time.sleep(2)
            else:
                logger.info(f"\n📖 已到达最后一个主页，共 {page_num} 页。")
                break

        if self.failed_images:
            logger.info(f"\n{'='*60}")
            logger.info(f"🔄 开始重试失败图片...")
            logger.info(f"{'='*60}")
            retry_success = 0
            for img_url, save_path in self.failed_images:
                if self._download_single_image(img_url, save_path):
                    retry_success += 1
            logger.info(f"✅ [重试完成] 图片成功 {retry_success}/{len(self.failed_images)}")

        total_duration = (time.time() - start_time) / 60
        logger.info(f"\n{'='*70}")
        logger.info(f"🏆 爬取任务全部完成！")
        logger.info(f"⏱️  总耗时: {total_duration:.1f} 分钟")
        logger.info(f"📊 处理套图总数: {total_albums}")
        logger.info(f"⚠️  最终失败: 图片 {len(self.failed_images)}")
        logger.info(f"📁 文件保存于: {self.save_path}")
        if IS_MOBILE:
            logger.info(f"📱 手机查找路径: 内部存储 → Download → 脚本爬取文件 → foamgirl")
        logger.info(f"{'='*70}")

def main():
    parser = argparse.ArgumentParser(description="FoamGirl 爬虫 - 分页修复版")
    parser.add_argument("--test", action="store_true", help="测试模式：使用默认路径，无需输入")
    parser.add_argument("--no-verify", action="store_true", help="关闭图片验证，加快下载速度")
    parser.add_argument("--save-dir", type=str, default="", help="自定义保存路径")
    args = parser.parse_args()

    if args.save_dir:
        save_path = args.save_dir
    elif args.test or IS_MOBILE:
        save_path = DEFAULT_SAVE_DIR_MOBILE
    else:
        default_pc = DEFAULT_SAVE_DIR_WIN
        user_input = input(f"请输入保存路径 (留空使用默认: {default_pc}): ").strip()
        save_path = user_input if user_input else default_pc

    spider = FoamGirlSpider(
        save_path=save_path
    )
    spider.run()

if __name__ == "__main__":
    main()
