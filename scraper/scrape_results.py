"""レース結果取得スクレイパー

netkeiba のレース結果ページから各レースのデータを取得し DB に保存する。
races / entries / results / horses / jockeys / trainers テーブルを更新する。

実行例:
    # 特定日のみ
    python scraper/scrape_results.py --date 20260406

    # 期間指定
    python scraper/scrape_results.py --date-from 20260401 --date-to 20260406

    # 年指定（初回バッチ向け）
    python scraper/scrape_results.py --year 2016
"""

import argparse
import logging
import re
from datetime import date

from scraper.db import (
    get_conn,
    insert_entry,
    insert_race,
    insert_result,
    race_exists,
    upsert_horse,
    upsert_jockey,
    upsert_trainer,
)
from scraper.scrape_calendar import fetch_race_ids_for_date
from scraper.utils import date_range, fetch_html, parse_float, parse_html, parse_int, parse_time_sec

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RESULT_URL = "https://race.netkeiba.com/race/result.html?race_id={race_id}"
HORSE_URL = "https://db.netkeiba.com/horse/{horse_id}/"

# JRA 10 場コード
VENUE_CODES = {
    "札幌": "01",
    "函館": "02",
    "福島": "03",
    "新潟": "04",
    "東京": "05",
    "中山": "06",
    "中京": "07",
    "京都": "08",
    "阪神": "09",
    "小倉": "10",
}

# 除外する障害コース
OBSTACLE_COURSE_KEYWORDS = ["障"]


def extract_venue_code(race_id: str) -> str:
    """race_id の場コード部分（3〜4桁目）を返す。"""
    return race_id[4:6]


def parse_distance_and_course(text: str) -> tuple[str | None, int | None, str | None]:
    """'芝1600m（右）' のような文字列をパースする。

    Returns:
        (course_type, distance, direction) のタプル
    """
    text = text.strip()

    # 障害は除外
    if any(kw in text for kw in OBSTACLE_COURSE_KEYWORDS):
        return None, None, None

    course_type = None
    if text.startswith("芝"):
        course_type = "芝"
    elif text.startswith("ダ"):
        course_type = "ダート"

    distance = None
    m = re.search(r"(\d+)m", text)
    if m:
        distance = int(m.group(1))

    direction = None
    if "右" in text:
        direction = "右"
    elif "左" in text:
        direction = "左"
    elif "直線" in text:
        direction = "直線"

    return course_type, distance, direction


def parse_race_info(soup, race_id: str, held_date: date) -> dict | None:
    """レース結果ページからレース基本情報をパースする。

    障害レースの場合は None を返す（除外対象）。
    """
    race = {
        "race_id": race_id,
        "held_date": held_date.strftime("%Y-%m-%d"),
        "venue_code": extract_venue_code(race_id),
        "race_num": parse_int(race_id[-2:]),
        "race_name": None,
        "course_type": None,
        "distance": None,
        "direction": None,
        "track_cond": None,
        "weather": None,
        "race_class": None,
        "age_cond": None,
        "sex_cond": None,
        "weight_type": None,
        "num_horses": None,
        "prize_1st": None,
    }

    # レース名
    name_tag = soup.select_one(".RaceName")
    if name_tag:
        race["race_name"] = name_tag.get_text(strip=True)

    # コース・距離・方向（例: 芝1600m（右））
    course_tag = soup.select_one(".RaceData01")
    if course_tag:
        spans = course_tag.find_all("span")
        for span in spans:
            text = span.get_text(strip=True)
            course_type, distance, direction = parse_distance_and_course(text)
            if course_type is None and distance is None:
                continue
            # 障害は None が返るので除外
            if course_type is None and "障" in text:
                logger.debug("Skip obstacle race: %s", race_id)
                return None
            race["course_type"] = course_type
            race["distance"] = distance
            race["direction"] = direction
            break

    # 馬場状態・天気（例: 天候:晴 芝:良）
    data_tag = soup.select_one(".RaceData01")
    if data_tag:
        text = data_tag.get_text()
        # 天候
        m = re.search(r"天候:(\S+)", text)
        if m:
            race["weather"] = m.group(1)
        # 馬場状態
        for keyword in ["芝:", "ダ:"]:
            m = re.search(rf"{keyword}(\S+)", text)
            if m:
                race["track_cond"] = m.group(1)
                break

    # クラス・条件（RaceData02 に含まれることが多い）
    data2_tag = soup.select_one(".RaceData02")
    if data2_tag:
        spans = data2_tag.find_all("span")
        texts = [s.get_text(strip=True) for s in spans]
        for text in texts:
            if "クラス" in text or any(
                kw in text for kw in ["G1", "G2", "G3", "オープン", "勝クラス", "未勝利", "新馬"]
            ):
                race["race_class"] = text
            if "歳" in text and ("以上" in text or "限定" in text or "以下" in text):
                race["age_cond"] = text
            if any(kw in text for kw in ["牡", "牝", "セン", "混合"]):
                race["sex_cond"] = text
            if any(kw in text for kw in ["馬齢", "ハンデ", "別定", "定量"]):
                race["weight_type"] = text

    return race


def parse_entries_and_results(soup, race_id: str) -> tuple[list[dict], list[dict]]:
    """レース結果テーブルをパースしてエントリーと結果を返す。"""
    entries = []
    results = []

    # 結果テーブル（.ResultTableWrap や .RaceTable_01 など）
    table = soup.select_one(".ResultTableWrap table") or soup.select_one("table.RaceTable_01")
    if not table:
        logger.warning("Result table not found for %s", race_id)
        return entries, results

    for row in table.select("tbody tr"):
        cols = row.find_all("td")
        if len(cols) < 10:
            continue

        try:
            finish_pos_text = cols[0].get_text(strip=True)
            horse_num_text = cols[2].get_text(strip=True)

            # 着順が数字でない行（取消・除外など）も処理
            finish_pos = parse_int(finish_pos_text)
            horse_num = parse_int(horse_num_text)

            if horse_num is None:
                continue

            # 馬 ID（馬名リンクから取得）
            horse_link = cols[3].select_one("a[href*='/horse/']")
            horse_id = None
            if horse_link:
                href = horse_link.get("href", "")
                m = re.search(r"/horse/(\w+)", href)
                if m:
                    horse_id = m.group(1)

            # 騎手 ID（騎手名リンクから取得）
            jockey_link = cols[6].select_one("a[href*='/jockey/']")
            jockey_id = None
            jockey_name = cols[6].get_text(strip=True)
            if jockey_link:
                href = jockey_link.get("href", "")
                m = re.search(r"/jockey/(\w+)", href)
                if m:
                    jockey_id = m.group(1)

            # 調教師 ID
            trainer_link = cols[18].select_one("a[href*='/trainer/']") if len(cols) > 18 else None
            trainer_id = None
            trainer_name = cols[18].get_text(strip=True) if len(cols) > 18 else None
            if trainer_link:
                href = trainer_link.get("href", "")
                m = re.search(r"/trainer/(\w+)", href)
                if m:
                    trainer_id = m.group(1)

            # 馬体重と増減（例: "450(-2)"）
            weight_text = cols[14].get_text(strip=True) if len(cols) > 14 else ""
            horse_weight = None
            weight_diff = None
            wm = re.match(r"(\d+)\(([+-]?\d+)\)", weight_text)
            if wm:
                horse_weight = int(wm.group(1))
                weight_diff = int(wm.group(2))

            entry = {
                "race_id": race_id,
                "horse_num": horse_num,
                "gate_num": parse_int(cols[1].get_text(strip=True)),
                "horse_id": horse_id,
                "jockey_id": jockey_id,
                "trainer_id": trainer_id,
                "burden_weight": parse_float(cols[5].get_text(strip=True)),
                "horse_weight": horse_weight,
                "weight_diff": weight_diff,
                "scratch": finish_pos_text in ["取消", "除外"],
            }

            # finish_status
            if finish_pos is not None:
                finish_status = "完走"
            elif finish_pos_text in ["取消", "除外"]:
                finish_status = finish_pos_text
            elif finish_pos_text in ["中止"]:
                finish_status = "中止"
            elif finish_pos_text in ["失格"]:
                finish_status = "失格"
            else:
                finish_status = "完走"

            result = {
                "race_id": race_id,
                "horse_num": horse_num,
                "finish_pos": finish_pos,
                "finish_status": finish_status,
                "time_sec": parse_time_sec(cols[7].get_text(strip=True)) if len(cols) > 7 else None,
                "margin": cols[8].get_text(strip=True) if len(cols) > 8 else None,
                "passing_order": cols[10].get_text(strip=True) if len(cols) > 10 else None,
                "last_3f": parse_float(cols[11].get_text(strip=True)) if len(cols) > 11 else None,
                "win_odds": parse_float(cols[12].get_text(strip=True)) if len(cols) > 12 else None,
                "popularity": parse_int(cols[13].get_text(strip=True)) if len(cols) > 13 else None,
            }

            entries.append(entry)
            results.append(result)

            # 騎手・調教師をキャッシュ用に紐づける
            row._jockey_id = jockey_id
            row._jockey_name = jockey_name
            row._trainer_id = trainer_id
            row._trainer_name = trainer_name

        except Exception as e:
            logger.warning("Failed to parse row in %s: %s", race_id, e)
            continue

    return entries, results


def scrape_one_race(race_id: str, held_date: date) -> None:
    """1 レース分のデータを取得して DB に保存する。"""
    url = RESULT_URL.format(race_id=race_id)
    html = fetch_html(url)
    soup = parse_html(html)

    with get_conn() as conn:
        # 既に取得済みならスキップ
        if race_exists(conn, race_id):
            logger.info("Skip (already exists): %s", race_id)
            return

        # レース基本情報
        race = parse_race_info(soup, race_id, held_date)
        if race is None:
            logger.info("Skip (obstacle race): %s", race_id)
            return

        insert_race(conn, race)

        # エントリー・結果のパース
        entries, results = parse_entries_and_results(soup, race_id)

        # 騎手・調教師・馬の保存（外部キー制約があるため entries より先に）
        table = soup.select_one(".ResultTableWrap table") or soup.select_one("table.RaceTable_01")
        if table:
            for row in table.select("tbody tr"):
                cols = row.find_all("td")
                if len(cols) < 7:
                    continue
                # 騎手
                jockey_link = cols[6].select_one("a[href*='/jockey/']")
                if jockey_link:
                    href = jockey_link.get("href", "")
                    m = re.search(r"/jockey/(\w+)", href)
                    if m:
                        upsert_jockey(conn, m.group(1), cols[6].get_text(strip=True), None)
                # 調教師
                if len(cols) > 18:
                    trainer_link = cols[18].select_one("a[href*='/trainer/']")
                    if trainer_link:
                        href = trainer_link.get("href", "")
                        m = re.search(r"/trainer/(\w+)", href)
                        if m:
                            upsert_trainer(conn, m.group(1), cols[18].get_text(strip=True), None)
                # 馬（最低限の情報で INSERT、詳細は別途取得）
                horse_link = cols[3].select_one("a[href*='/horse/']") if len(cols) > 3 else None
                if horse_link:
                    href = horse_link.get("href", "")
                    m = re.search(r"/horse/(\w+)", href)
                    if m:
                        horse_id = m.group(1)
                        horse_name = cols[3].get_text(strip=True)
                        upsert_horse(
                            conn,
                            {
                                "horse_id": horse_id,
                                "horse_name": horse_name,
                                "sex": None,
                                "coat_color": None,
                                "birthday": None,
                                "father_id": None,
                                "mother_id": None,
                                "trainer_id": None,
                                "owner": None,
                                "breeder": None,
                            },
                        )

        for entry in entries:
            insert_entry(conn, entry)
        for result in results:
            insert_result(conn, result)

        logger.info("Saved: %s (%d entries)", race_id, len(entries))


def scrape_results(start_date: date, end_date: date) -> None:
    """指定期間のレース結果を取得して DB に保存する。"""
    logger.info("Scraping results from %s to %s", start_date, end_date)

    for target_date in date_range(start_date, end_date):
        race_ids = fetch_race_ids_for_date(target_date)
        if not race_ids:
            logger.debug("No races on %s", target_date)
            continue

        logger.info("%s: %d races found", target_date, len(race_ids))
        for race_id in race_ids:
            try:
                scrape_one_race(race_id, target_date)
            except Exception as e:
                logger.error("Error scraping %s: %s", race_id, e)
                continue  # 1レース失敗しても続行

    logger.info("Results scraping completed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="レース結果取得")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="特定日 (YYYYMMDD)")
    group.add_argument("--year", type=int, help="年指定 (例: 2016)")
    group.add_argument("--date-from", dest="date_from", help="開始日 (YYYYMMDD)")

    parser.add_argument("--date-to", dest="date_to", help="終了日 (YYYYMMDD、--date-from と併用)")

    args = parser.parse_args()

    if args.date:
        d = date(int(args.date[:4]), int(args.date[4:6]), int(args.date[6:8]))
        scrape_results(d, d)
    elif args.year:
        scrape_results(date(args.year, 1, 1), date(args.year, 12, 31))
    elif args.date_from:
        start = date(int(args.date_from[:4]), int(args.date_from[4:6]), int(args.date_from[6:8]))
        if args.date_to:
            end = date(int(args.date_to[:4]), int(args.date_to[4:6]), int(args.date_to[6:8]))
        else:
            from datetime import date as date_cls

            end = date_cls.today()
        scrape_results(start, end)


if __name__ == "__main__":
    main()
