"""出馬表スクレイパー

netkeiba の出馬表ページから出走馬情報を取得し entries / horses テーブルに保存する。
前日 17:00〜18:00 に実行することを想定。

実行例:
    # 特定日の出馬表取得
    python scraper/scrape_shutuba.py --date 20260406

    # 期間指定
    python scraper/scrape_shutuba.py --date-from 20260406 --date-to 20260407
"""

import argparse
import logging
import re
from datetime import date

from scraper.db import get_conn, upsert_entry, upsert_horse, upsert_jockey, upsert_trainer
from scraper.scrape_calendar import fetch_race_ids_for_date
from scraper.utils import date_range, fetch_html, parse_html, parse_int

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SHUTUBA_URL = "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"


def parse_shutuba(soup, race_id: str) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """出馬表ページをパースして出走馬情報を返す。

    Returns:
        (entries, horses, jockeys, trainers) のタプル
    """
    entries = []
    horses = []
    jockeys = []
    trainers = []

    # 出馬表テーブル（.Shutuba_Table または .RaceTable_01）
    table = soup.select_one(".Shutuba_Table") or soup.select_one("table.RaceTable_01")
    if not table:
        logger.warning("Shutuba table not found for %s", race_id)
        return entries, horses, jockeys, trainers

    for row in table.select("tbody tr"):
        cols = row.find_all("td")
        if len(cols) < 6:
            continue

        try:
            gate_num = parse_int(cols[0].get_text(strip=True))
            horse_num = parse_int(cols[1].get_text(strip=True))

            if horse_num is None:
                continue

            # 馬 ID
            horse_link = cols[3].select_one("a[href*='/horse/']")
            horse_id = None
            horse_name = cols[3].get_text(strip=True)
            if horse_link:
                m = re.search(r"/horse/(\w+)", horse_link.get("href", ""))
                if m:
                    horse_id = m.group(1)

            # 騎手 ID（col index は出馬表レイアウトによって異なる場合がある）
            jockey_id = None
            jockey_name = None
            for col in cols[5:8]:
                jockey_link = col.select_one("a[href*='/jockey/']")
                if jockey_link:
                    m = re.search(r"/jockey/(\w+)", jockey_link.get("href", ""))
                    if m:
                        jockey_id = m.group(1)
                        jockey_name = jockey_link.get_text(strip=True)
                    break

            # 斤量（burden_weight）
            burden_weight = None
            for col in cols[5:8]:
                text = col.get_text(strip=True)
                try:
                    val = float(text)
                    if 40.0 <= val <= 65.0:  # 斤量の妥当範囲
                        burden_weight = val
                        break
                except ValueError:
                    continue

            # 調教師 ID
            trainer_id = None
            trainer_name = None
            for col in cols[6:]:
                trainer_link = col.select_one("a[href*='/trainer/']")
                if trainer_link:
                    m = re.search(r"/trainer/(\w+)", trainer_link.get("href", ""))
                    if m:
                        trainer_id = m.group(1)
                        trainer_name = trainer_link.get_text(strip=True)
                    break

            entry = {
                "race_id": race_id,
                "horse_num": horse_num,
                "gate_num": gate_num,
                "horse_id": horse_id,
                "jockey_id": jockey_id,
                "trainer_id": trainer_id,
                "burden_weight": burden_weight,
                "horse_weight": None,  # 出馬表時点では未確定
                "weight_diff": None,
                "scratch": False,
            }
            entries.append(entry)

            if horse_id:
                horses.append(
                    {
                        "horse_id": horse_id,
                        "horse_name": horse_name,
                        "sex": None,
                        "coat_color": None,
                        "birthday": None,
                        "father_id": None,
                        "mother_id": None,
                        "trainer_id": trainer_id,
                        "owner": None,
                        "breeder": None,
                    }
                )

            if jockey_id and jockey_name:
                jockeys.append((jockey_id, jockey_name))

            if trainer_id and trainer_name:
                trainers.append((trainer_id, trainer_name))

        except Exception as e:
            logger.warning("Failed to parse shutuba row in %s: %s", race_id, e)
            continue

    return entries, horses, jockeys, trainers


def scrape_shutuba_for_date(target_date: date) -> None:
    """指定日の全レース出馬表を取得して DB に保存する。"""
    race_ids = fetch_race_ids_for_date(target_date)
    if not race_ids:
        logger.info("%s: 開催なし", target_date)
        return

    logger.info("%s: %d races", target_date, len(race_ids))

    for race_id in race_ids:
        try:
            url = SHUTUBA_URL.format(race_id=race_id)
            html = fetch_html(url)
            soup = parse_html(html)
            entries, horses, jockeys, trainers = parse_shutuba(soup, race_id)

            with get_conn() as conn:
                for jockey_id, jockey_name in jockeys:
                    upsert_jockey(conn, jockey_id, jockey_name, None)
                for trainer_id, trainer_name in trainers:
                    upsert_trainer(conn, trainer_id, trainer_name, None)
                for horse in horses:
                    upsert_horse(conn, horse)
                for entry in entries:
                    upsert_entry(conn, entry)

            logger.info("Saved shutuba: %s (%d entries)", race_id, len(entries))

        except Exception as e:
            logger.error("Error scraping shutuba %s: %s", race_id, e)
            continue


def scrape_shutuba(start_date: date, end_date: date) -> None:
    """指定期間の出馬表を取得する。"""
    logger.info("Scraping shutuba from %s to %s", start_date, end_date)
    for target_date in date_range(start_date, end_date):
        scrape_shutuba_for_date(target_date)
    logger.info("Shutuba scraping completed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="出馬表取得")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="特定日 (YYYYMMDD)")
    group.add_argument("--date-from", dest="date_from", help="開始日 (YYYYMMDD)")

    parser.add_argument("--date-to", dest="date_to", help="終了日 (YYYYMMDD、--date-from と併用)")

    args = parser.parse_args()

    if args.date:
        d = date(int(args.date[:4]), int(args.date[4:6]), int(args.date[6:8]))
        scrape_shutuba(d, d)
    elif args.date_from:
        start = date(int(args.date_from[:4]), int(args.date_from[4:6]), int(args.date_from[6:8]))
        end = (
            date(int(args.date_to[:4]), int(args.date_to[4:6]), int(args.date_to[6:8]))
            if args.date_to
            else date.today()
        )
        scrape_shutuba(start, end)


if __name__ == "__main__":
    main()
