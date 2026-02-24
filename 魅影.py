# -*- coding: utf-8 -*-
""" 魅影图库爬虫 双平台通用版（手机Termux+Windows电脑）| 顺序下载 | 40KB过滤 | 魔法数字校验 """
import requests
from bs4 import BeautifulSoup
import concurrent.futures
from PIL import Image
import io
import os
import re
import sys
import time
import random
import argparse
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# -------- 日志设置（双平台：终端+文件，中文兼容） --------
log_file = "魅影图库_crawler.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# 禁用第三方库日志
for name in logging.root.manager.loggerDict:
    if name not in ['__main__', 'crawler']:
        logging.getLogger(name).setLevel(logging.CRITICAL)

# -------- 跨平台核心配置 --------
IS_MOBILE = os.path.exists("/sdcard/Download")  # 自动识别手机/Windows
MIN_IMAGE_SIZE = 40 * 1024  # 40KB文件过滤
# 跨平台默认保存路径
DEFAULT_SAVE_DIR_MOBILE = "/sdcard/Download/魅影图库"
DEFAULT_SAVE_DIR_WIN = r"C:\爬取结果\魅影图库"

class GalleryCrawler:
    def __init__(self, save_path, verify=False):
        self.save_path = save_path
        self.verify = verify
        self.session = self._create_session()
        # 初始化列表
        self.completed_list = []
        self.failed_list = []
        self.processed_album_urls = set()  # 记录已处理专辑，避免重复
        # 创建保存目录（跨平台兼容）
        os.makedirs(self.save_path, exist_ok=True)
        logger.info(f"📂 创建/检测保存目录: {self.save_path}")

    def _create_session(self):
        """创建带连接池和重试机制的session，跨平台兼容"""
        session = requests.Session()
        retry = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=100, pool_maxsize=100)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        # 设置headers，模拟浏览器+防盗链
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': 'https://xxtu.org/',
            'Accept-Language': 'zh-CN,zh;q=0.9'
        })
        return session

    def _sanitize_filename(self, filename):
        """跨平台文件名清洗，过滤所有特殊字符"""
        return re.sub(r'[\\/:*?"<>|+@#$%^&*(){}[]·~!￥]', ' ', filename)

    def get_single_page_albums(self, page):
        """获取单页相册链接和名称，用于顺序爬取"""
        base_url = "https://xxtu.org/"
        try:
            # 构建分页URL
            current_url = base_url if page == 1 else f"{base_url}?paged={page}"
            logger.info(f"\n📄 正在获取第 {page} 页相册，URL: {current_url}")
            # 随机延迟4-8秒，防反爬
            delay = random.uniform(4, 8)
            logger.info(f"⏱️  随机延迟 {delay:.1f} 秒...")
            time.sleep(delay)

            # 获取页面内容
            start_time = time.time()
            response = self.session.get(current_url, timeout=30)
            response.raise_for_status()
            end_time = time.time()

            # 计算下载信息
            content_length = len(response.content)
            elapsed_time = end_time - start_time
            if elapsed_time > 0:
                speed = content_length / elapsed_time / 1024  # KB/s
                logger.info(f"📥 页面下载完成，大小: {content_length/1024:.1f} KB，耗时: {elapsed_time:.2f} 秒，速度: {speed:.1f} KB/s")

            # 解析页面
            logger.info(f"🔍 正在解析页面...")
            soup = BeautifulSoup(response.text, 'html.parser')
            # 查找所有相册项
            album_items = soup.find_all('article') or soup.find_all('div', class_='post')
            if not album_items:
                logger.info(f"第 {page} 页未找到相册项")
                return [], None

            logger.info(f"✅ 第 {page} 页找到 {len(album_items)} 个相册项")
            # 提取相册信息
            albums = []
            for item in album_items:
                a_tag = item.find('a')
                if a_tag and 'href' in a_tag.attrs:
                    album_url = a_tag['href']
                    if album_url in self.processed_album_urls:
                        continue
                    # 查找相册名称，多标签兼容
                    title_tag = item.find('h2', class_='entry-title') or item.find('h1', class_='entry-title') or item.find('h3', class_='entry-title')
                    if title_tag:
                        album_name = title_tag.text.strip()
                        sanitized_name = self._sanitize_filename(album_name)
                        albums.append((sanitized_name, album_name, album_url))
                        self.processed_album_urls.add(album_url)
                        logger.info(f"🎉 检索到新相册: {album_name}")

            # 检查下一页
            next_page = page + 1 if len(albums) > 0 else None
            return albums, next_page

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.info(f"第 {page} 页返回404，已到达最后一页")
                return [], None
            else:
                logger.error(f"HTTP请求失败: {e}，请按任意键重试...")
                input()
                return [], page
        except requests.exceptions.RequestException as e:
            logger.error(f"网络请求失败: {e}，请按任意键重试...")
            input()
            return [], page
        except Exception as e:
            logger.error(f"获取第 {page} 页相册失败: {e}")
            import traceback
            traceback.print_exc()
            return [], None

    def validate_image(self, image_path):
        """验证图片是否损坏，先检查大小再校验完整性"""
        # 先过滤小于40KB的文件
        if os.path.getsize(image_path) < MIN_IMAGE_SIZE:
            logger.error(f"图片 {image_path} 小于40KB，判定为无效")
            return False
        # 验证图片完整性
        try:
            with Image.open(image_path) as img:
                img.verify()
            return True
        except Exception as e:
            logger.error(f"图片 {image_path} 损坏: {e}")
            return False

    def download_image(self, image_url, save_path):
        """下载单张图片，40KB过滤+魔法数字校验+原子化写入"""
        retry_count = 0
        max_retries = 5
        img_name = os.path.basename(save_path)
        # 跨平台清洗图片名
        img_name = self._sanitize_filename(img_name)
        save_path = os.path.join(os.path.dirname(save_path), img_name)

        while retry_count < max_retries:
            try:
                # 随机延迟4-8秒
                delay = random.uniform(4, 8)
                logger.info(f"[{img_name}] ⏱️  随机延迟 {delay:.1f} 秒...")
                time.sleep(delay)

                # 发送请求
                logger.info(f"[{img_name}] 🔗 正在连接: {image_url}")
                response = self.session.get(image_url, timeout=60, stream=True)
                response.raise_for_status()

                # 验证响应内容是否为图片
                content_type = response.headers.get('Content-Type', '')
                if not content_type.startswith('image/'):
                    logger.error(f"[{img_name}] ❌ 返回非图片内容: {content_type}")
                    retry_count += 1
                    continue

                # 检查文件是否已存在
                if os.path.exists(save_path):
                    if self.validate_image(save_path):
                        logger.info(f"[{img_name}] ✅ 已存在且完整，跳过")
                        return True
                    else:
                        logger.info(f"[{img_name}] 🔄 已存在但损坏，重新下载")

                # 原子化写入文件（跨平台目录兼容）
                temp_path = save_path + '.tmp'
                downloaded_size = 0
                start_time = time.time()

                with open(temp_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            # 简单进度显示
                            elapsed_time = time.time() - start_time
                            if elapsed_time > 0.5:
                                speed = downloaded_size / elapsed_time / 1024
                                logger.info(f"[{img_name}] 📊 下载中: {downloaded_size/1024:.1f} KB | 速度: {speed:.1f} KB/s", end='\r')

                print()  # 换行结束进度条
                # 过滤小于40KB的文件
                if downloaded_size < MIN_IMAGE_SIZE:
                    logger.error(f"[{img_name}] ❌ 文件过小({downloaded_size/1024:.1f} KB < 40KB)，丢弃")
                    os.remove(temp_path)
                    retry_count += 1
                    continue

                # 魔法数字验证（图片格式校验）
                logger.info(f"[{img_name}] 🔍 验证图片完整性...")
                with open(temp_path, 'rb') as f:
                    magic_number = f.read(8)
                valid_magic_numbers = {b'\xFF\xD8\xFF', b'\x89\x50\x4E\x47', b'\x47\x49\x46\x38', b'\x42\x4D'}
                is_valid = any(magic_number.startswith(m) for m in valid_magic_numbers)
                if not is_valid:
                    logger.error(f"[{img_name}] ❌ 魔法数字无效，丢弃")
                    os.remove(temp_path)
                    retry_count += 1
                    continue

                # 最终验证并重命名
                if self.validate_image(temp_path):
                    os.rename(temp_path, save_path)
                    logger.info(f"[{img_name}] ✅ 下载完成，保存至: {save_path}")
                    return True
                else:
                    logger.error(f"[{img_name}] ❌ 下载后损坏，重试({retry_count+1}/{max_retries})")
                    os.remove(temp_path)
                    retry_count += 1
            except requests.exceptions.RequestException as e:
                retry_count += 1
                logger.error(f"[{img_name}] ❌ 网络失败: {e}，重试({retry_count}/{max_retries})")
                time.sleep(random.uniform(4, 8))
            except Exception as e:
                retry_count += 1
                logger.error(f"[{img_name}] ❌ 下载失败: {e}，重试({retry_count}/{max_retries})")
                time.sleep(random.uniform(4, 8))

        logger.error(f"[{img_name}] ❌ 5次重试均失败，放弃下载")
        return False

    def download_album(self, album_info, album_index, total_album):
        """下载单个相册，带进度标识，跨平台兼容"""
        sanitized_name, original_name, album_url = album_info
        logger.info(f"\n{'='*60}")
        logger.info(f"🎊 [专辑 {album_index}/{total_album}] 开始处理: {original_name}")
        logger.info(f"📚 相册链接: {album_url}")
        logger.info(f"{'='*60}")

        # 创建相册目录（跨平台）
        album_dir = os.path.join(self.save_path, sanitized_name)
        os.makedirs(album_dir, exist_ok=True)

        try:
            # 随机延迟4-8秒
            delay = random.uniform(4, 8)
            logger.info(f"⏱️  随机延迟 {delay:.1f} 秒...")
            time.sleep(delay)

            # 获取相册页面内容
            response = self.session.get(album_url, timeout=30)
            response.raise_for_status()

            # 解析相册页面，获取所有图片链接
            logger.info(f"🔍 提取图片链接...")
            soup = BeautifulSoup(response.text, 'html.parser')
            image_urls = []
            for img in soup.find_all('img'):
                if 'src' in img.attrs and img['src'].endswith(('.jpg', '.jpeg', '.png', '.gif')):
                    image_urls.append(img['src'])

            total_images = len(image_urls)
            logger.info(f"📸 相册包含 {total_images} 张有效图片")
            if total_images == 0:
                logger.error(f"❌ 无有效图片，加入失败列表")
                self.failed_list.append(original_name
