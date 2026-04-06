"""オッズスクレイパー（Playwright使用）

netkeiba のオッズページから単勝・複勝オッズを取得し odds テーブルに保存する。
オッズは JS でリアルタイム更新されるため Playwright を使用する。

実行例:
    # 特定日のオッズ取得（1回スナップショット）
    python scraper/scrape_odds.py --date 20260406

    # 特定 race_id のみ
    python scraper/scrape_odds.py --race-id 202606030401
"""

import argparse
import logging
import re
from datetime import UTC, datetime

from scraper.db import get_conn, insert_odds
from scraper.scrape_calendar import fetch_race_ids_for_date
from scraper.utils import parse_html, parse_int

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ODDS_WIN_URL = "https://race.netkeiba.com/odds/index.html?type=b1&race_id={race_id}"
ODDS_PLACE_URL = "https://race.netkeiba.com/odds/index.html?type=b2&race_id={race_id}"


def fetch_odds_html(race_id: str, odds_type: str) -> str:
    """Playwright で指定タイプのオッズページ HTML を取得する。

    Args:
        race_id: レース ID
        odds_type: 'win'（単勝）または 'place'（複勝）
    """
    if odds_type == "win":
        url = ODDS_WIN_URL.format(race_id=race_id)
        selector = "#odds_tan_b, .OddsTable, [id*='odds']"
    else:
        url = ODDS_PLACE_URL.format(race_id=race_id)
        selector = "#odds_fuku_b, .OddsTable, [id*='odds']"

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, timeout=60000)
            page.wait_for_load_state("load", timeout=30000)
            try:
                page.wait_for_selector(selector, timeout=15000)
            except Exception:
                logger.warning("Odds selector not found for %s (%s)", race_id, odds_type)
            html = page.content()
        finally:
            browser.close()

    return html


def parse_win_odds(soup, race_id: str, fetched_at: datetime) -> list[dict]:
    """単勝オッズをパースする。"""
    odds_list = []

    # 単勝オッズテーブル
    table = (
        soup.select_one("#odds_tan_b table")
        or soup.select_one(".OddsTable")
        or soup.select_one("table.RaceOdds_HorseList_Table")
    )
    if not table:
        logger.warning("Win odds table not found for %s", race_id)
        return odds_list

    for row in table.select("tr"):
        cols = row.find_all("td")
        if len(cols) < 2:
            continue

        # 馬番
        horse_num = None
        for col in cols[:3]:
            val = parse_int(col.get_text(strip=True))
            if val and 1 <= val <= 28:
                horse_num = val
                break

        if horse_num is None:
            continue

        # オッズ値（倍率）
        odds_val = None
        for col in cols:
            text = col.get_text(strip=True)
            # オッズの形式: "3.5" や "100.0" など
            m = re.match(r"^(\d+\.\d+)$", text)
            if m:
                val = float(m.group(1))
                if val >= 1.0:  # 1.0 未満はオッズとしてあり得ない
                    odds_val = val
                    break

        if odds_val is None:
            continue

        odds_list.append(
            {
                "race_id": race_id,
                "horse_num": horse_num,
                "odds_type": "win",
                "odds_low": odds_val,
                "odds_high": None,
                "fetched_at": fetched_at,
            }
        )

    return odds_list


def parse_place_odds(soup, race_id: str, fetched_at: datetime) -> list[dict]:
    """複勝オッズをパースする（下限〜上限の範囲）。"""
    odds_list = []

    table = (
        soup.select_one("#odds_fuku_b table")
        or soup.select_one(".OddsTable")
        or soup.select_one("table.RaceOdds_HorseList_Table")
    )
    if not table:
        logger.warning("Place odds table not found for %s", race_id)
        return odds_list

    for row in table.select("tr"):
        cols = row.find_all("td")
        if len(cols) < 2:
            continue

        # 馬番
        horse_num = None
        for col in cols[:3]:
            val = parse_int(col.get_text(strip=True))
            if val and 1 <= val <= 28:
                horse_num = val
                break

        if horse_num is None:
            continue

        # 複勝オッズ（"1.5 - 2.3" または "1.5〜2.3" 形式）
        odds_low = None
        odds_high = None
        for col in cols:
            text = col.get_text(strip=True)
            # "1.5 - 2.3" 形式
            m = re.search(r"(\d+\.\d+)\s*[-〜～]\s*(\d+\.\d+)", text)
            if m:
                odds_low = float(m.group(1))
                odds_high = float(m.group(2))
                break
            # 単一値（確定後など）
            m2 = re.match(r"^(\d+\.\d+)$", text)
            if m2:
                val = float(m2.group(1))
                if val >= 1.0:
                    odds_low = val
                    break

        if odds_low is None:
            continue

        odds_list.append(
            {
                "race_id": race_id,
                "horse_num": horse_num,
                "odds_type": "place",
                "odds_low": odds_low,
                "odds_high": odds_high,
                "fetched_at": fetched_at,
            }
        )

    return odds_list


def scrape_odds_for_race(race_id: str) -> None:
    """1レース分の単勝・複勝オッズを取得して DB に保存する。"""
    fetched_at = datetime.now(tz=UTC)

    # 単勝
    try:
        html = fetch_odds_html(race_id, "win")
        soup = parse_html(html)
        win_odds = parse_win_odds(soup, race_id, fetched_at)
    except Exception as e:
        logger.error("Error fetching win odds for %s: %s", race_id, e)
        win_odds = []

    # 複勝
    try:
        html = fetch_odds_html(race_id, "place")
        soup = parse_html(html)
        place_odds = parse_place_odds(soup, race_id, fetched_at)
    except Exception as e:
        logger.error("Error fetching place odds for %s: %s", race_id, e)
        place_odds = []

    with get_conn() as conn:
        for odds in win_odds + place_odds:
            insert_odds(conn, odds)

    logger.info(
        "Saved odds: %s (win=%d, place=%d) at %s",
        race_id,
        len(win_odds),
        len(place_odds),
        fetched_at.strftime("%H:%M:%S"),
    )


def scrape_odds_for_date(target_date) -> None:
    """指定日の全レース分のオッズを取得する。"""
    race_ids = fetch_race_ids_for_date(target_date)
    if not race_ids:
        logger.info("%s: 開催なし", target_date)
        return

    logger.info("%s: %d races", target_date, len(race_ids))
    for race_id in race_ids:
        try:
            scrape_odds_for_race(race_id)
        except Exception as e:
            logger.error("Error scraping odds %s: %s", race_id, e)
            continue


def main() -> None:
    parser = argparse.ArgumentParser(description="オッズ取得")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="特定日 (YYYYMMDD)")
    group.add_argument("--race-id", dest="race_id", help="特定 race_id")

    args = parser.parse_args()

    if args.date:
        from datetime import date

        d = date(int(args.date[:4]), int(args.date[4:6]), int(args.date[6:8]))
        scrape_odds_for_date(d)
    elif args.race_id:
        scrape_odds_for_race(args.race_id)


if __name__ == "__main__":
    main()
