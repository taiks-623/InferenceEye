"""開催カレンダー取得スクレイパー

netkeiba のレース一覧ページを確認し、開催日を race_calendars テーブルに保存する。
レース一覧ページは JavaScript で動的レンダリングされるため Playwright を使用する。

実行例:
    # 特定日の確認
    python scraper/scrape_calendar.py --date 20260406

    # 期間指定
    python scraper/scrape_calendar.py --date-from 20260101 --date-to 20261231

    # 年指定
    python scraper/scrape_calendar.py --year 2026
"""

import argparse
import logging
import re
from datetime import date

from playwright.sync_api import sync_playwright

from scraper.db import get_conn, upsert_race_calendar
from scraper.utils import date_range, parse_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RACE_LIST_URL = "https://race.netkeiba.com/top/race_list.html?kaisai_date={date}"


def fetch_race_ids_for_date(target_date: date) -> list[str]:
    """指定日のレース一覧ページから race_id 一覧を取得する（Playwright使用）。

    レースが存在しない日（非開催日）は空リストを返す。
    """
    date_str = target_date.strftime("%Y%m%d")
    url = RACE_LIST_URL.format(date=date_str)

    try:
        with sync_playwright() as p:
            browser = p.firefox.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
            html = page.content()
            browser.close()
    except Exception as e:
        logger.warning("Failed to fetch calendar for %s: %s", date_str, e)
        return []

    soup = parse_html(html)
    race_ids = []

    for a_tag in soup.select("a[href*='race_id=']"):
        href = a_tag.get("href", "")
        m = re.search(r"race_id=(\d{12})", href)
        if m:
            race_ids.append(m.group(1))

    return sorted(set(race_ids))


def is_race_day(target_date: date) -> bool:
    """指定日が競馬開催日かどうかを判定する。"""
    return len(fetch_race_ids_for_date(target_date)) > 0


def scrape_calendar(start_date: date, end_date: date) -> None:
    """指定期間の開催カレンダーを取得して DB に保存する。"""
    logger.info("Scraping calendar from %s to %s", start_date, end_date)

    with get_conn() as conn:
        for target_date in date_range(start_date, end_date):
            date_str = target_date.strftime("%Y-%m-%d")
            scheduled = is_race_day(target_date)
            upsert_race_calendar(conn, date_str, scheduled)
            status = "開催" if scheduled else "非開催"
            logger.info("%s: %s", date_str, status)

    logger.info("Calendar scraping completed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="開催カレンダー取得")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="特定日 (YYYYMMDD)")
    group.add_argument("--year", type=int, help="年指定 (例: 2026)")
    group.add_argument("--date-from", dest="date_from", help="開始日 (YYYYMMDD)")

    parser.add_argument("--date-to", dest="date_to", help="終了日 (YYYYMMDD、--date-from と併用)")

    args = parser.parse_args()

    if args.date:
        d = date(int(args.date[:4]), int(args.date[4:6]), int(args.date[6:8]))
        scrape_calendar(d, d)
    elif args.year:
        scrape_calendar(date(args.year, 1, 1), date(args.year, 12, 31))
    elif args.date_from:
        start = date(int(args.date_from[:4]), int(args.date_from[4:6]), int(args.date_from[6:8]))
        if args.date_to:
            end = date(int(args.date_to[:4]), int(args.date_to[4:6]), int(args.date_to[6:8]))
        else:
            end = date.today()
        scrape_calendar(start, end)


if __name__ == "__main__":
    main()
