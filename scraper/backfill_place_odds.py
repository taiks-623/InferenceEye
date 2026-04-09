"""複勝オッズ バックフィルスクリプト

results テーブルの place_odds が NULL のレースを対象に、
netkeiba の払戻テーブルから複勝配当を取得して更新する。

実行例:
    python scraper/backfill_place_odds.py
    python scraper/backfill_place_odds.py --year 2020
    python scraper/backfill_place_odds.py --date-from 20200101 --date-to 20201231
"""

import argparse
import logging
from datetime import date

from scraper.db import get_conn
from scraper.scrape_results import RESULT_URL, parse_place_payouts
from scraper.utils import fetch_html, parse_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def fetch_races_missing_place_odds(
    conn, start_date: date | None = None, end_date: date | None = None
) -> list[str]:
    """place_odds が NULL の race_id を返す。"""
    with conn.cursor() as cur:
        if start_date and end_date:
            cur.execute(
                """
                SELECT DISTINCT r.race_id
                FROM races r
                JOIN results res ON r.race_id = res.race_id
                WHERE res.place_odds IS NULL
                  AND r.held_date BETWEEN %s AND %s
                ORDER BY r.race_id
                """,
                (start_date, end_date),
            )
        else:
            cur.execute(
                """
                SELECT DISTINCT r.race_id
                FROM races r
                JOIN results res ON r.race_id = res.race_id
                WHERE res.place_odds IS NULL
                ORDER BY r.race_id
                """
            )
        return [row[0] for row in cur.fetchall()]


def update_place_odds(conn, race_id: str, payouts: dict[int, float]) -> int:
    """race_id の results に place_odds を UPDATE する。更新行数を返す。"""
    if not payouts:
        return 0
    updated = 0
    with conn.cursor() as cur:
        for horse_num, place_odds in payouts.items():
            cur.execute(
                """
                UPDATE results SET place_odds = %s
                WHERE race_id = %s AND horse_num = %s
                """,
                (place_odds, race_id, horse_num),
            )
            updated += cur.rowcount
    return updated


def backfill(start_date: date | None = None, end_date: date | None = None) -> None:
    with get_conn() as conn:
        race_ids = fetch_races_missing_place_odds(conn, start_date, end_date)

    logger.info("Found %d races missing place_odds", len(race_ids))

    ok = 0
    failed = 0
    for race_id in race_ids:
        try:
            html = fetch_html(RESULT_URL.format(race_id=race_id))
            soup = parse_html(html)
            payouts = parse_place_payouts(soup)
            with get_conn() as conn:
                n = update_place_odds(conn, race_id, payouts)
            logger.info("Updated %s: %d rows (payouts=%s)", race_id, n, payouts)
            ok += 1
        except Exception as e:
            logger.error("Failed %s: %s", race_id, e)
            failed += 1

    logger.info("Done: %d updated, %d failed", ok, failed)


def main() -> None:
    parser = argparse.ArgumentParser(description="複勝オッズ バックフィル")
    parser.add_argument("--year", type=int, help="年指定")
    parser.add_argument("--date-from", dest="date_from", help="開始日 (YYYYMMDD)")
    parser.add_argument("--date-to", dest="date_to", help="終了日 (YYYYMMDD)")
    args = parser.parse_args()

    start_date = end_date = None
    if args.year:
        start_date = date(args.year, 1, 1)
        end_date = date(args.year, 12, 31)
    elif args.date_from:
        start_date = date(
            int(args.date_from[:4]), int(args.date_from[4:6]), int(args.date_from[6:8])
        )
        end_date = (
            date(int(args.date_to[:4]), int(args.date_to[4:6]), int(args.date_to[6:8]))
            if args.date_to
            else date.today()
        )

    backfill(start_date, end_date)


if __name__ == "__main__":
    main()
