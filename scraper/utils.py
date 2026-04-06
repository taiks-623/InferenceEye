"""スクレイピング共通ユーティリティ"""

import logging
import random
import time
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def fetch_html(
    url: str, max_retries: int = 3, sleep_range: tuple[float, float] = (1.0, 2.0)
) -> str:
    """指定 URL の HTML を取得する。失敗時は指数バックオフでリトライ。

    Args:
        url: 取得先 URL
        max_retries: 最大リトライ回数
        sleep_range: リクエスト前のスリープ時間の範囲（秒）

    Returns:
        HTML 文字列

    Raises:
        requests.HTTPError: リトライ後も失敗した場合
    """
    time.sleep(random.uniform(*sleep_range))

    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            response.encoding = response.apparent_encoding
            return response.text
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                logger.error("Failed to fetch %s after %d retries: %s", url, max_retries, e)
                raise
            wait = 2**attempt
            logger.warning(
                "Retry %d/%d for %s (wait %ds): %s", attempt + 1, max_retries, url, wait, e
            )
            time.sleep(wait)

    raise RuntimeError("Unreachable")  # mypy 向け


def parse_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def date_range(start: date, end: date):
    """start から end まで（end 含む）の date を1日ずつ yield する。"""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def parse_time_sec(time_str: str) -> float | None:
    """'1:23.4' 形式のタイムを秒数（float）に変換する。

    Args:
        time_str: '1:23.4' または '23.4' 形式の文字列

    Returns:
        秒数。変換できない場合は None。
    """
    time_str = time_str.strip()
    if not time_str or time_str == "---":
        return None
    try:
        if ":" in time_str:
            minutes, seconds = time_str.split(":")
            return int(minutes) * 60 + float(seconds)
        return float(time_str)
    except ValueError:
        return None


def parse_int(text: str) -> int | None:
    """文字列を int に変換する。変換できない場合は None。"""
    try:
        return int(text.strip().replace(",", ""))
    except (ValueError, AttributeError):
        return None


def parse_float(text: str) -> float | None:
    """文字列を float に変換する。変換できない場合は None。"""
    try:
        return float(text.strip().replace(",", ""))
    except (ValueError, AttributeError):
        return None
