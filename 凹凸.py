#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""" 凸凹吧爬虫 双平台通用版（手机Termux+Windows电脑）| 顺序下载 | 40KB过滤 """
import os
import re
import time
import random
import argparse
import logging
import io
import sys
from urllib.parse import urljoin, urlparse
from typing import List, Tuple, Dict, Optional, Set, Any
import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException
from bs4 import BeautifulSoup

# -------- 检查 Pillow 库 --------
try:
    from PIL import Image, ImageFile
    from PIL.Image import UnidentifiedImageError
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    Image.MAX_IMAGE_PIXELS = None
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

# -------- 跨平台默认配置 --------
BASE_URL = "https://www.tuao.cc/"
CATEGORIES = [
    ("最新", "/Articles"),
    ("无圣光", "/Articles/Categories/1"),
    ("凸凹图", "/Articles/Categories/2"),
    ("靓人体", "/Articles/Categories/3"),
    ("写真集", "/Articles/Categories/4")
]
# 自动识别平台 - 手机(Termux)/Windows
IS_MOBILE = os.path.exists("/sdcard/Download")
# 跨平台默认保存路径
DEFAULT_SAVE_DIR_MOBILE = "/sdcard/Download/凹凸吧"
DEFAULT_SAVE_DIR_WIN = r"C:\爬取结果\凹凸吧"
DEFAULT_SAVE_DIR = DEFAULT_SAVE_DIR_MOBILE if IS_MOBILE else DEFAULT_SAVE_DIR_WIN

DEFAULT_RETRIES = 5
DEFAULT_TIMEOUT = 20
DEFAULT_CONCURRENCY_IMAGE = 4  # 专辑内并发下载图片数
DEFAULT_PAGE_SLEEP_MIN = 4.0
DEFAULT_PAGE_SLEEP_MAX = 8.0
DEFAULT_ALBUM_SLEEP_MIN = 4.0
DEFAULT_ALBUM_SLEEP_MAX = 8.0
DEFAULT_POOL_SIZE = 32
MIN_IMAGE_SIZE = 40 * 1024  # 40KB，过滤小于此大小的文件

# -------- 日志设置（终端+文件，双平台兼容） --------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("凹凸吧_crawler.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
if not PILLOW_AVAILABLE:
    logging.warning("Pillow 库未安装，跳过图片完整性校验！")
    logging.warning("手机Termux安装：pkg install python-pillow | Windows安装：pip install pillow")

# -------- 辅助函数 --------
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Referer": BASE_URL,
        "Accept-Language": "zh-CN,zh;q=0.9"
    })
    adapter = HTTPAdapter(
        pool_connections=DEFAULT_POOL_SIZE,
        pool_maxsize=DEFAULT_POOL_SIZE
    )
    s.mount('http://', adapter)
    s.mount('https://', adapter)
    return s

def get_random_delay(min_delay: float, max_delay: float) -> float:
    return random.uniform(min_delay, max_delay)

def request_with_retry(session: requests.Session, url: str, retries: int, timeout: int, is_binary: bool = False) -> Optional[Any]:
    r: Optional[requests.Response] = None
    for attempt in range(1, retries + 1):
        try:
            if is_binary:
                r = session.get(url, timeout=timeout, stream=True)
            else:
                r = session.get(url, timeout=timeout)
            r.raise_for_status()
            return r.content if is_binary else r.text
        except RequestException as e:
            wait_time = get_random_delay(4.0, 8.0)
            status_msg = f"{r.status_code}" if r is not None else "无响应"
            if attempt < retries:
                logging.warning("请求失败: %s (尝试 %d/%d) 错误: %s。状态: %s，等待 %.1fs 并重试。", url, attempt, retries, e, status_msg, wait_time)
                time.sleep(wait_time)
            else:
                logging.error("请求失败: %s (所有尝试均失败)。错误: %s", url, e)
                input("按任意键继续...")
    return None

def sanitize_filename(name: str, maxlen: int = 150) -> str:
    if not name:
        return "untitled"
    # 跨平台特殊字符过滤（兼容Windows/Android）
    s = re.sub(r'[\\/:*?"<>|+@#$%^&*(){}[]]', "_", name).strip()
    return s[:maxlen] or "untitled"

# -------- 图像验证辅助函数 --------
def is_image_valid_bytes(data: bytes, verify: bool) -> bool:
    if not verify or not PILLOW_AVAILABLE:
        return True
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        return True
    except Exception as e:
        logging.debug("图片内容无效: %s", e)
        return False

def is_image_valid_file(filepath: str, verify: bool) -> bool:
    if not verify or not PILLOW_AVAILABLE:
        return True
    try:
        with Image.open(filepath) as img:
            img.verify()
        return True
    except Exception as e:
        logging.debug("文件损坏: %s", e)
        return False

def save_bytes_atomic(path: str, data: bytes) -> bool:
    tmp_path = path + ".part"
    try:
        # 确保目录存在（跨平台兼容）
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp_path, "wb") as f:
            f.write(data)
        os.replace(tmp_path, path)
        return True
    except IOError as e:
        logging.error("写入失败 %s: %s", path, e)
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False

# -------- 解析函数 --------
def parse_next_page(soup: BeautifulSoup) -> Optional[str]:
    pagination = soup.find("ul", class_="pagination")
    if not pagination:
        return None
    next_page = pagination.find("a", string="»")
    if next_page and "href" in next_page.attrs:
        return next_page["href"]
    return None

def parse_albums_on_listing_page(html: str, base_url: str) -> List[Tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    albums = []
    img_links = soup.find_all("a", class_="index-imgcontent-img")
    title_links = soup.find_all("a", class_="index-imgcontent-title")
    for i, img_link in enumerate(img_links):
        img_href = img_link.get("href")
        if not img_href:
            continue
        album_url = urljoin(base_url, img_href)
        album_title = ""
        if i < len(title_links):
            album_title = title_links[i].get_text(strip=True)
        if not album_title:
            album_title = f"专辑_{i+1}"
        album_title = sanitize_filename(album_title)
        albums.append((album_title, album_url))
    return albums

def parse_images_on_album_page(html: str, base_url: str) -> Set[str]:
    soup = BeautifulSoup(html, "html.parser")
    image_urls = set()
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src.startswith("/Files/images/") and (src.endswith(".webp") or src.endswith(".jpg") or src.endswith(".png")):
            image_urls.add(urljoin(base_url, src))
    logging.info(f"✅ 本页抓到图片数: {len(image_urls)}")
    return image_urls

# -------- 下载核心逻辑 --------
def download_single_image(session: requests.Session, url: str, album_dir: str, verify: bool, retries: int, timeout: int, current_index: int, total_images: int) -> str:
    filename = os.path.basename(urlparse(url).path)
    # 过滤文件名特殊字符（跨平台）
    filename = sanitize_filename(filename)
    dest_path = os.path.join(album_dir, filename)
    progress_prefix = f"({current_index}/{total_images})"

    if os.path.exists(dest_path):
        if is_image_valid_file(dest_path, verify):
            logging.info("%s 已存在，跳过: %s", progress_prefix, dest_path)
            return "skipped"
        else:
            logging.warning("%s 文件损坏，重新下载", progress_prefix)
            try:
                os.remove(dest_path)
            except:
                pass

    data = request_with_retry(session, url, retries=retries, timeout=timeout, is_binary=True)
    if not data:
        logging.warning("%s 下载失败: %s", progress_prefix, url)
        return "fail"

    # 过滤小于 40KB 的文件
    if len(data) < MIN_IMAGE_SIZE:
        logging.warning("%s 文件太小 (%d bytes < 40KB)，丢弃: %s", progress_prefix, len(data), url)
        return "fail"

    if not is_image_valid_bytes(data, verify):
        logging.warning("%s 图片无效，丢弃: %s", progress_prefix, url)
        return "fail"

    if save_bytes_atomic(dest_path, data):
        logging.info("✅ %s 下载成功: %s", progress_prefix, dest_path)
        return "ok"
    else:
        logging.warning("❌ %s 保存失败", progress_prefix)
        return "fail"

def parse_album_total_pages(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    pagination = soup.find("ul", class_="pagination")
    if not pagination:
        return 1
    page_numbers = {1}
    for li in pagination.find_all("li"):
        a_tag = li.find("a")
        if a_tag:
            text = a_tag.get_text(strip=True)
            if text.isdigit():
                page_numbers.add(int(text))
    return max(page_numbers) if page_numbers else 1

def process_album(session: requests.Session, title: str, url: str, save_root: str, verify: bool, retries: int, timeout: int) -> Dict[str, int]:
    """处理单个专辑：进入专辑页 -> 提取图片 -> 并发下载图片"""
    time.sleep(get_random_delay(DEFAULT_ALBUM_SLEEP_MIN, DEFAULT_ALBUM_SLEEP_MAX))
    log_prefix = f"[专辑] {title}"
    logging.info("%s → 开始处理", log_prefix)

    album_html = request_with_retry(session, url, retries=retries, timeout=timeout)
    if not album_html:
        logging.error("%s 无法获取页面", log_prefix)
        return {"ok":0,"skipped":0,"fail":1}

    total_pages = parse_album_total_pages(album_html)
    logging.info("%s → 共 %d 页", log_prefix, total_pages)

    all_image_urls = set()
    for page_num in range(1, total_pages+1):
        if page_num == 1:
            page_url = url
        else:
            page_url = f"{url}?page={page_num}"
        logging.info("%s → 抓取分页 %d/%d", log_prefix, page_num, total_pages)
        page_html = request_with_retry(session, page_url, retries=retries, timeout=timeout)
        if not page_html:
            continue
        imgs = parse_images_on_album_page(page_html, BASE_URL)
        all_image_urls.update(imgs)
        time.sleep(1)

    logging.info("%s → 总共抓到 %d 张图片", log_prefix, len(all_image_urls))
    if not all_image_urls:
        return {"ok":0,"skipped":0,"fail":0}

    album_dir = os.path.join(save_root, title)
    os.makedirs(album_dir, exist_ok=True)
    results = {"ok":0,"skipped":0,"fail":0}
    indexed = list(enumerate(all_image_urls, start=1))

    # 专辑内并发下载图片
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=DEFAULT_CONCURRENCY_IMAGE) as executor:
        future_map = {
            executor.submit(download_single_image, session, img_url, album_dir, verify, retries, timeout, idx, len(all_image_urls)): img_url
            for idx, img_url in indexed
        }
        for f in as_completed(future_map):
            try:
                res = f.result()
                results[res] += 1
            except:
                results["fail"] += 1

    logging.info("%s → 完成：成功 %d 跳过 %d 失败 %d", log_prefix, results["ok"], results["skipped"], results["fail"])
    return results

# -------- 主函数：双平台通用+顺序执行 --------
def main():
    parser = argparse.ArgumentParser(description="凸凹吧爬虫（双平台通用版 | 手机Termux+Windows）")
    parser.add_argument("--verify", action="store_true", default=True, help="开启图片完整性校验（默认开启）")
    parser.add_argument("--no-verify", action="store_false", dest="verify", help="关闭图片完整性校验，加快下载")
    parser.add_argument("--test", action="store_true", help="测试模式：使用默认路径，无需手动输入")
    parser.add_argument("--save-dir", type=str, default="", help="自定义保存路径（跨平台兼容，如/sdcard/Download/xxx 或 C:/xxx）")
    args = parser.parse_args()

    # 配置保存路径
    if args.save_dir:
        save_dir = args.save_dir
    else:
        save_dir = DEFAULT_SAVE_DIR
    # 确保目录存在
    os.makedirs(save_dir, exist_ok=True)

    # 打印启动信息（双平台）
    logging.info("="*70)
    logging.info(f"📱 运行平台：{'手机Termux' if IS_MOBILE else 'Windows电脑'}")
    logging.info(f"📂 保存路径：{save_dir}")
    logging.info(f"🔍 图片校验：{'开启' if args.verify and PILLOW_AVAILABLE else '关闭'}")
    logging.info(f"⚡ 过滤规则：小于40KB的文件自动丢弃")
    logging.info("="*70)

    session = make_session()
    summary = {"ok":0,"skipped":0,"fail":0,"albums_processed":0}
    seen_album_urls: Set[str] = set()

    # 遍历所有分类
    for category_index, (category_name, category_path) in enumerate(CATEGORIES, 1):
        logging.info("\n" + "="*60)
        logging.info(f"[{category_index}/{len(CATEGORIES)}] 正在处理分类: {category_name}")
        logging.info("="*60)

        category_url = urljoin(BASE_URL, category_path)
        current_url = category_url
        page_count = 1

        # 遍历当前分类的所有列表页
        while True:
            logging.info(f"[分类: {category_name}] 列表页 {page_count}: {current_url}")
            list_html = request_with_retry(session, current_url, retries=DEFAULT_RETRIES, timeout=DEFAULT_TIMEOUT)
            if not list_html:
                logging.warning("获取列表页失败: %s", current_url)
                time.sleep(get_random_delay(DEFAULT_PAGE_SLEEP_MIN, DEFAULT_PAGE_SLEEP_MAX))
                break

            # 解析当前页的所有专辑
            current_page_albums = parse_albums_on_listing_page(list_html, BASE_URL)
            logging.info(f"[分类: {category_name}] 列表页 {page_count} 发现 {len(current_page_albums)} 个专辑")

            # 顺序处理当前页的每个专辑：获取一个，下载一个
            for album_title, album_url in current_page_albums:
                if album_url in seen_album_urls:
                    logging.info(f"跳过已处理专辑: {album_title}")
                    continue
                seen_album_urls.add(album_url)

                # 立即下载这个专辑
                result = process_album(
                    session,
                    album_title,
                    album_url,
                    save_dir,
                    args.verify,
                    DEFAULT_RETRIES,
                    DEFAULT_TIMEOUT
                )

                # 更新统计
                summary["ok"] += result["ok"]
                summary["skipped"] += result["skipped"]
                summary["fail"] += result["fail"]
                summary["albums_processed"] += 1

            # 检查下一页
            soup = BeautifulSoup(list_html, "html.parser")
            next_page_url = parse_next_page(soup)
            if next_page_url:
                current_url = urljoin(BASE_URL, next_page_url)
                page_count += 1
                logging.info(f"[{category_name}] 发现下一页，将继续爬取第 {page_count} 页")
                time.sleep(get_random_delay(DEFAULT_PAGE_SLEEP_MIN, DEFAULT_PAGE_SLEEP_MAX))
            else:
                logging.info(f"[{category_index}/{len(CATEGORIES)}] 类型 {category_name} 已完成所有分页爬取")
                break

    # 打印最终结果
    logging.info("\n" + "="*70)
    logging.info("程序执行完毕。爬取任务总结：")
    logging.info(f" 处理的专辑总数: {summary['albums_processed']}")
    logging.info("-" * 25)
    logging.info(f" [成功下载]: {summary['ok']} 张")
    logging.info(f" [跳过 (已存在)]: {summary['skipped']} 张")
    logging.info(f" [失败总数]: {summary['fail']} 张")
    logging.info("="*70)
    logging.info(f"所有图片已保存到: {save_dir}")
    if IS_MOBILE:
        logging.info("📱 手机文件管理器查找：内部存储 → Download → 凹凸吧")
    else:
        logging.info("💻 Windows查找：此电脑 → C盘 → 爬取结果 → 凹凸吧")

if __name__ == "__main__":
    main()
