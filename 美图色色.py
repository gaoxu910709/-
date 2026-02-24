# -*- coding: utf-8 -*-
""" 美图色色爬虫 双平台通用版（手机Termux+Windows电脑）| 顺序下载 | 40KB过滤 """
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

# -------- 日志设置（双平台兼容：终端+文件） --------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("美图色色_crawler.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# -------- 跨平台核心配置 --------
IS_MOBILE = os.path.exists("/sdcard/Download")  # 自动识别手机/Windows
# 跨平台默认保存路径
DEFAULT_SAVE_DIR_MOBILE = "/sdcard/Download/美图色色"
DEFAULT_SAVE_DIR_WIN = r"C:\爬取结果\美图色色"
MIN_IMAGE_SIZE = 40 * 1024  # 40KB过滤

class MeituSpider:
    def __init__(self, save_path, verify=False, page_sleep=5, album_sleep=3):
        self.save_path = save_path
        self.verify = verify
        self.page_sleep = page_sleep
        self.album_sleep = album_sleep
        self.session = self._init_session()
        self.base_url = "https://xn--drdgbhrb-xx6n10qjm3s.tljkd-01.sbs"
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.114 Safari/537.36"
        ]
        self.failed_images = []
        self.failed_albums = []
        self.processed_album_urls = set()  # 记录已处理专辑，避免重复

    def _init_session(self):
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=100, pool_maxsize=100)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _get_random_user_agent(self):
        return random.choice(self.user_agents)

    def _get_response(self, url, retries=5):
        headers = {
            "User-Agent": self._get_random_user_agent(),
            "Referer": self.base_url,
            "Accept-Language": "zh-CN,zh;q=0.9"
        }
        
        for i in range(retries):
            start_time = time.time()
            try:
                response = self.session.get(url, headers=headers, timeout=30)
                response.raise_for_status()
                request_time = time.time() - start_time
                logger.info(f"[请求] {url} 成功，耗时 {request_time:.2f}秒")
                
                delay = random.uniform(2, 4)
                time.sleep(delay)
                return response
            except Exception as e:
                request_time = time.time() - start_time
                if i < retries - 1:
                    delay = random.uniform(4, 8)
                    logger.warning(f"[请求] {url} 失败，{i+1}/{retries} 重试，{delay:.2f}秒后重试: {e}")
                    time.sleep(delay)
                else:
                    logger.error(f"[请求] {url} 失败，{retries} 次重试后仍失败: {e}")
                    return None

    def _parse_albums(self, url):
        """解析单页相册列表，返回本页所有专辑"""
        response = self._get_response(url)
        if not response:
            logger.error(f"[解析] 请求失败，无法解析页面: {url}")
            return [], None
        
        try:
            soup = BeautifulSoup(response.text, "html.parser")
            albums = []
            album_elements = soup.select(".videos-list-wrap .video-item-col")
            logger.info(f"[解析] 本页找到 {len(album_elements)} 个相册元素")
            
            for i, album in enumerate(album_elements):
                try:
                    album_url = album.get("href")
                    album_title = album.select_one(".video-desc-content").text.strip() if album.select_one(".video-desc-content") else f"未知专辑_{i+1}"
                    # 跨平台过滤专辑名特殊字符
                    album_title = re.sub(r'[\\/:*?"<>|+@#$%^&*(){}[]]', "_", album_title)
                    if album_url and album_title not in ["/", ""]:
                        full_url = f"{self.base_url}{album_url}" if not album_url.startswith("http") else album_url
                        albums.append((album_title, full_url))
                        logger.debug(f"[解析] 解析到相册: {album_title} - {full_url}")
                except Exception as e:
                    logger.error(f"[解析] 解析相册元素失败: {e}")
            
            # 解析下一页
            next_page = None
            next_page_element = soup.select_one(".mo-paging .paging-item--next")
            if next_page_element and next_page_element.get("href"):
                next_page = f"{self.base_url}{next_page_element.get('href')}" if not next_page_element.get("href").startswith("http") else next_page_element.get("href")
            return albums, next_page
        except Exception as e:
            logger.error(f"[解析] 解析页面失败: {url}, 错误: {e}")
            return [], None

    def _parse_album_images(self, album_url):
        logger.info(f"[解析] 开始解析相册图片: {album_url}")
        response = self._get_response(album_url)
        if not response:
            return []
        
        soup = BeautifulSoup(response.text, "html.parser")
        book_pages = soup.select_one("#book-pages")
        if not book_pages:
            logger.error(f"[解析] 未找到相册图片容器: {album_url}")
            return []
        
        screenshots = book_pages.get("data-screenshots", "")
        if not screenshots:
            logger.error(f"[解析] 未找到相册图片URL: {album_url}")
            return []
        
        images = []
        for img_url in screenshots.split("#$"):
            img_url = img_url.strip().lstrip('$')
            if img_url and img_url.startswith("http"):
                images.append(img_url)
        logger.info(f"[解析] 从相册提取到 {len(images)} 张图片")
        return images

    def _validate_image(self, image_path):
        try:
            with Image.open(image_path) as img:
                img.verify()
            return True
        except Exception:
            return False

    def _download_image(self, img_url, save_path):
        if os.path.exists(save_path):
            logger.info(f"[下载] 图片已存在，跳过: {save_path}")
            return True
        
        logger.info(f"[下载] 开始下载: {img_url}")
        response = self._get_response(img_url)
        if response is None:
            self.failed_images.append((img_url, save_path))
            return False
        
        # 过滤非图片/过小文件
        content_type = response.headers.get("Content-Type", "")
        file_size = len(response.content)
        if not content_type.startswith("image/"):
            logger.error(f"[下载] 非图片内容，丢弃: {img_url}")
            self.failed_images.append((img_url, save_path))
            return False
        if file_size < MIN_IMAGE_SIZE:  # 40KB过滤
            logger.error(f"[下载] 文件过小({file_size}字节)，丢弃: {img_url}")
            self.failed_images.append((img_url, save_path))
            return False
        
        # 原子化写入（跨平台目录兼容）
        save_dir = os.path.dirname(save_path)
        os.makedirs(save_dir, exist_ok=True)
        temp_path = f"{save_path}.tmp"
        try:
            with open(temp_path, "wb") as f:
                f.write(response.content)
        except Exception as e:
            logger.error(f"[下载] 写入失败: {e}")
            self.failed_images.append((img_url, save_path))
            return False
        
        # 图片验证
        if self.verify:
            if not self._validate_image(temp_path):
                logger.error(f"[下载] 图片损坏，丢弃: {img_url}")
                os.remove(temp_path)
                self.failed_images.append((img_url, save_path))
                return False
        
        # 重命名完成下载
        try:
            os.rename(temp_path, save_path)
            logger.info(f"[下载] 成功保存: {save_path}")
            return True
        except Exception as e:
            logger.error(f"[下载] 重命名失败: {e}")
            os.remove(temp_path)
            self.failed_images.append((img_url, save_path))
            return False

    def _download_album(self, album_info, album_index, total_album):
        """下载单个专辑"""
        album_title, album_url = album_info
        if album_url in self.processed_album_urls:
            logger.info(f"[专辑 {album_index}/{total_album}] 已处理，跳过: {album_title}")
            return True
        self.processed_album_urls.add(album_url)
        
        logger.info(f"\n[专辑 {album_index}/{total_album}] 开始处理: {album_title}")
        # 清洗专辑名，跨平台路径兼容
        safe_title = re.sub(r'[<>"/\\|?*:]', "_", album_title)[:50] if album_title else f"专辑_{album_index}"
        album_dir = os.path.join(self.save_path, safe_title)
        os.makedirs(album_dir, exist_ok=True)
        
        # 解析图片并下载
        images = self._parse_album_images(album_url)
        if not images:
            logger.error(f"[专辑 {album_index}/{total_album}] 无图片，加入失败列表")
            self.failed_albums.append(album_info)
            return False
        
        # 单专辑内并发下载图片（低并发防反爬）
        image_failures = 0
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = []
            for img_index, img_url in enumerate(images):
                img_name = f"{img_index+1:03d}.jpg"
                img_path = os.path.join(album_dir, img_name)
                futures.append(executor.submit(self._download_image, img_url, img_path))
            
            for future in as_completed(futures):
                if not future.result():
                    image_failures += 1
        
        logger.info(f"[专辑 {album_index}/{total_album}] 处理完成: 成功{len(images)-image_failures}张 | 失败{image_failures}张")
        time.sleep(self.album_sleep)  # 专辑间延迟，反爬
        return True

    def _retry_failed(self):
        """重试失败的图片和专辑"""
        total_failed = len(self.failed_albums) + len(self.failed_images)
        if total_failed == 0:
            return
        logger.info(f"\n[重试] 开始处理失败项: 专辑{len(self.failed_albums)}个 | 图片{len(self.failed_images)}张")
        # 重试专辑
        for idx, album in enumerate(self.failed_albums, 1):
            self._download_album(album, idx, len(self.failed_albums))
        # 重试图片
        success = 0
        for img_url, save_path in self.failed_images:
            if self._download_image(img_url, save_path):
                success += 1
        logger.info(f"[重试] 完成: 图片成功{success}/{len(self.failed_images)}张")

    def run(self):
        """主运行逻辑：顺序爬取+下载"""
        start_total_time = time.time()
        logger.info("="*50)
        logger.info(f"📱 运行平台：{'手机Termux' if IS_MOBILE else 'Windows电脑'}")
        logger.info("[主程序] 美图色色爬虫 - 双平台通用版")
        logger.info(f"[主程序] 保存路径: {self.save_path}")
        logger.info(f"[主程序] 图片验证: {self.verify} | 列表页延迟: {self.page_sleep}s")
        logger.info(f"[主程序] 过滤规则: 小于40KB文件自动丢弃")
        logger.info("="*50)
        os.makedirs(self.save_path, exist_ok=True)

        # 初始化爬取参数
        current_url = "https://xn--drdgbhrb-xx6n10qjm3s.tljkd-01.sbs/t/13/"
        page = 1
        processed_pages = set()
        total_album_count = 0  # 累计总专辑数

        # 核心：循环爬取列表页 → 逐个处理本页专辑 → 爬取下一页
        while current_url and current_url not in processed_pages:
            processed_pages.add(current_url)
            logger.info(f"\n[列表页] 开始爬取第 {page} 页: {current_url}")
            # 解析本页所有专辑
            page_albums, next_page = self._parse_albums(current_url)
            if not page_albums:
                logger.warning(f"[列表页] 第{page}页无专辑，跳至下一页")
                current_url = next_page
                page += 1
                time.sleep(self.page_sleep)
                continue
            
            # 顺序处理本页每个专辑：获取一个，下载一个
            total_album_count += len(page_albums)
            for idx, album in enumerate(page_albums, 1):
                self._download_album(album, total_album_count - len(page_albums) + idx, total_album_count)
            
            # 跳至下一页
            if next_page and next_page not in processed_pages:
                logger.info(f"[列表页] 第{page}页处理完成，跳至下一页")
                current_url = next_page
                page += 1
                time.sleep(self.page_sleep)
            else:
                logger.info(f"[列表页] 已爬取所有页面（共{page}页）")
                current_url = None

        # 重试失败项
        self._retry_failed()

        # 最终统计
        total_time = time.time() - start_total_time
        logger.info("\n" + "="*50)
        logger.info("[主程序] 爬取完成！")
        logger.info(f"[主程序] 总耗时: {total_time/60:.1f}分钟 | 处理专辑: {total_album_count}个")
        logger.info(f"[主程序] 失败项: 专辑{len(self.failed_albums)}个 | 图片{len(self.failed_images)}张")
        logger.info(f"[主程序] 图片保存路径: {self.save_path}")
        if IS_MOBILE:
            logger.info("📱 手机查找：内部存储 → Download → 美图色色")
        else:
            logger.info("💻 Windows查找：此电脑 → C盘 → 爬取结果 → 美图色色")
        logger.info("="*50)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="美图色色爬虫-双平台通用版（手机Termux+Windows）")
    parser.add_argument("--page-sleep", type=float, default=5, help="列表页翻页延迟(秒)，默认5")
    parser.add_argument("--album-sleep", type=float, default=3, help="专辑间下载延迟(秒)，默认3")
    parser.add_argument("--no-verify", action="store_true", help="关闭图片验证，加快下载速度")
    parser.add_argument("--test", action="store_true", help="测试模式：使用默认路径，无需输入")
    parser.add_argument("--save-dir", type=str, default="", help="自定义保存路径（如/sdcard/Download/xxx 或 C:/xxx）")
    args = parser.parse_args()

    # 配置保存路径
    if args.save_dir:
        save_path = args.save_dir
    else:
        save_path = DEFAULT_SAVE_DIR_MOBILE if IS_MOBILE else DEFAULT_SAVE_DIR_WIN
    # 图片验证开关
    verify = not args.no_verify

    # 启动爬虫
    spider = MeituSpider(
        save_path=save_path,
        verify=verify,
        page_sleep=args.page_sleep,
        album_sleep=args.album_sleep
    )
    spider.run()
