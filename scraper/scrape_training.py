"""調教タイムスクレイパー

netkeiba の調教タイムページから各馬の調教データを取得し training_times テーブルに保存する。
前日 13:30 に実行することを想定。

実行例:
    # 特定日のレース向け調教データ取得
    python scraper/scrape_training.py --date 20260406

    # 期間指定
    python scraper/scrape_training.py --date-from 20260406 --date-to 20260407
"""

import argparse
import logging
import re
from datetime import date

from scraper.db import get_conn, upsert_training_time
from scraper.scrape_calendar import fetch_race_ids_for_date
from scraper.utils import date_range, fetch_html, parse_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TRAINING_URL = "https://race.netkeiba.com/race/oikiri.html?race_id={race_id}"


def parse_training(soup, race_id: str) -> list[dict]:
    """調教タイムページをパースして調教データを返す。"""
    trainings = []

    # 調教テーブル（.OikiriTable や .TrainingTable）
    table = (
        soup.select_one(".OikiriTable")
        or soup.select_one("table.Oikiri_Table")
        or soup.select_one(".TrainingTable")
    )
    if not table:
        logger.warning("Training table not found for %s", race_id)
        return trainings

    for row in table.select("tbody tr"):
        cols = row.find_all("td")
        if len(cols) < 6:
            continue

        try:
            # 馬 ID（馬名リンクから）
            horse_link = row.select_one("a[href*='/horse/']")
            if not horse_link:
                continue
            m = re.search(r"/horse/(\w+)", horse_link.get("href", ""))
            if not m:
                continue
            horse_id = m.group(1)

            # 各列を取得（列構成はページレイアウトによる）
            texts = [col.get_text(strip=True) for col in cols]

            # 調教日（YYYY/MM/DD または MM/DD 形式）
            training_date = None
            for text in texts:
                m_date = re.search(r"(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})", text)
                if m_date:
                    training_date = (
                        f"{m_date.group(1)}-{int(m_date.group(2)):02d}-{int(m_date.group(3)):02d}"
                    )
                    break
                m_date2 = re.search(r"(\d{1,2})[/\-](\d{1,2})", text)
                if m_date2:
                    # 年なしの場合は race_id の年を使う
                    year = int(race_id[:4])
                    training_date = (
                        f"{year}-{int(m_date2.group(1)):02d}-{int(m_date2.group(2)):02d}"
                    )
                    break

            if not training_date:
                continue

            # 調教場（栗東/美浦/ノーザンF等）
            venue_code = None
            for text in texts:
                if any(kw in text for kw in ["栗東", "美浦", "ノーザン", "社台", "札幌", "函館"]):
                    venue_code = text
                    break

            # コース種別（坂路/CW/DP/芝/ウッド等）
            course_type = None
            for text in texts:
                if any(
                    kw in text for kw in ["坂路", "CW", "DP", "芝", "ダート", "ウッド", "プール"]
                ):
                    course_type = text
                    break

            # タイム（4F, 3F, 1F）
            time_values = []
            for text in texts:
                m_time = re.match(r"^(\d+\.\d+)$", text)
                if m_time:
                    time_values.append(float(m_time.group(1)))

            time_4f = time_values[0] if len(time_values) > 0 else None
            time_3f = time_values[1] if len(time_values) > 1 else None
            time_1f = time_values[2] if len(time_values) > 2 else None

            # ランク（S/A/B/C等）
            rank = None
            for text in texts:
                if re.match(r"^[SABC]$", text):
                    rank = text
                    break

            # 騎乗者
            jockey_rider = None
            for col in cols:
                jockey_link = col.select_one("a[href*='/jockey/']")
                if jockey_link:
                    jockey_rider = jockey_link.get_text(strip=True)
                    break

            trainings.append(
                {
                    "horse_id": horse_id,
                    "training_date": training_date,
                    "venue_code": venue_code,
                    "course_type": course_type,
                    "time_4f": time_4f,
                    "time_3f": time_3f,
                    "time_1f": time_1f,
                    "rank": rank,
                    "jockey_rider": jockey_rider,
                    "note": None,
                }
            )

        except Exception as e:
            logger.warning("Failed to parse training row in %s: %s", race_id, e)
            continue

    return trainings


def scrape_training_for_date(target_date: date) -> None:
    """指定日の全レース分の調教タイムを取得して DB に保存する。"""
    race_ids = fetch_race_ids_for_date(target_date)
    if not race_ids:
        logger.info("%s: 開催なし", target_date)
        return

    logger.info("%s: %d races", target_date, len(race_ids))

    for race_id in race_ids:
        try:
            url = TRAINING_URL.format(race_id=race_id)
            html = fetch_html(url)
            soup = parse_html(html)
            trainings = parse_training(soup, race_id)

            with get_conn() as conn:
                for training in trainings:
                    upsert_training_time(conn, training)

            logger.info("Saved training: %s (%d rows)", race_id, len(trainings))

        except Exception as e:
            logger.error("Error scraping training %s: %s", race_id, e)
            continue


def scrape_training(start_date: date, end_date: date) -> None:
    """指定期間の調教タイムを取得する。"""
    logger.info("Scraping training from %s to %s", start_date, end_date)
    for target_date in date_range(start_date, end_date):
        scrape_training_for_date(target_date)
    logger.info("Training scraping completed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="調教タイム取得")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="特定日 (YYYYMMDD)")
    group.add_argument("--date-from", dest="date_from", help="開始日 (YYYYMMDD)")

    parser.add_argument("--date-to", dest="date_to", help="終了日 (YYYYMMDD、--date-from と併用)")

    args = parser.parse_args()

    if args.date:
        d = date(int(args.date[:4]), int(args.date[4:6]), int(args.date[6:8]))
        scrape_training(d, d)
    elif args.date_from:
        start = date(int(args.date_from[:4]), int(args.date_from[4:6]), int(args.date_from[6:8]))
        end = (
            date(int(args.date_to[:4]), int(args.date_to[4:6]), int(args.date_to[6:8]))
            if args.date_to
            else date.today()
        )
        scrape_training(start, end)


if __name__ == "__main__":
    main()
