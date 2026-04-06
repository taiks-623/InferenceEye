"""netkeiba 掲示板スクレイパー

各馬の netkeiba 掲示板からコメントを取得する。
発走10分前に実行し、取得したコメントを AI フィルター（ai_filter）に渡す。
掲示板の内容は DB には保存せず、テキストとして返す。

実行例:
    # 特定 race_id の出走馬コメント取得
    python scraper/scrape_bbs.py --race-id 202606030401

    # 特定日の全レース
    python scraper/scrape_bbs.py --date 20260406
"""

import argparse
import logging
import re
from datetime import date, datetime, timedelta

from scraper.db import get_conn
from scraper.scrape_calendar import fetch_race_ids_for_date
from scraper.utils import fetch_html, parse_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BBS_URL = "https://community.netkeiba.com/?pid=community&id={horse_id}"


def get_horse_ids_for_race(race_id: str) -> list[tuple[int, str]]:
    """指定 race_id の出走馬 (horse_num, horse_id) 一覧を DB から取得する。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT horse_num, horse_id FROM entries WHERE race_id = %s AND horse_id IS NOT NULL ORDER BY horse_num",
                (race_id,),
            )
            return cur.fetchall()


def parse_bbs_comments(soup, horse_id: str, since: datetime | None = None) -> list[str]:
    """掲示板ページから近日のコメントを抽出する。

    Args:
        soup: BeautifulSoup オブジェクト
        horse_id: 馬 ID
        since: この日時以降のコメントのみ取得（None なら全件）

    Returns:
        コメント文字列のリスト
    """
    comments = []

    # コメントコンテナ（複数のセレクタを試みる）
    items = (
        soup.select(".Community_DetailList_Item")
        or soup.select(".bbs_item")
        or soup.select(".CommentList li")
        or soup.select(".community_list_detail")
    )

    for item in items:
        try:
            # 投稿日時（発走40分前〜10分前 の範囲が理想だが、全件取得してフィルタ）
            date_tag = item.select_one(".Community_DetailList_Date, .date, time")
            post_dt = None
            if date_tag:
                text = date_tag.get_text(strip=True)
                # "2026/04/05 10:30" 形式
                m = re.search(r"(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})\s+(\d{1,2}):(\d{2})", text)
                if m:
                    post_dt = datetime(
                        int(m.group(1)),
                        int(m.group(2)),
                        int(m.group(3)),
                        int(m.group(4)),
                        int(m.group(5)),
                    )

            if since and post_dt and post_dt < since:
                continue

            # コメント本文
            body_tag = item.select_one(".Community_DetailList_Body, .comment, .body, p")
            if body_tag:
                text = body_tag.get_text(strip=True)
                if text:
                    comments.append(text)

        except Exception as e:
            logger.warning("Failed to parse bbs comment for %s: %s", horse_id, e)
            continue

    return comments


def scrape_bbs_for_race(race_id: str, hours_back: int = 12) -> dict[int, list[str]]:
    """指定 race_id の全出走馬の掲示板コメントを取得する。

    Args:
        race_id: レース ID
        hours_back: 何時間前からのコメントを取得するか（デフォルト 12 時間）

    Returns:
        {horse_num: [comment, ...]} の辞書
    """
    horse_entries = get_horse_ids_for_race(race_id)
    if not horse_entries:
        logger.warning("No entries found for race %s. Run scrape_shutuba first.", race_id)
        return {}

    since = datetime.now() - timedelta(hours=hours_back)
    result: dict[int, list[str]] = {}

    for horse_num, horse_id in horse_entries:
        try:
            url = BBS_URL.format(horse_id=horse_id)
            html = fetch_html(url)
            soup = parse_html(html)
            comments = parse_bbs_comments(soup, horse_id, since=since)
            result[horse_num] = comments
            logger.debug("BBS %s horse_num=%d: %d comments", race_id, horse_num, len(comments))
        except Exception as e:
            logger.error("Error fetching bbs for horse %s: %s", horse_id, e)
            result[horse_num] = []
            continue

    logger.info(
        "BBS %s: %d horses, total %d comments",
        race_id,
        len(result),
        sum(len(v) for v in result.values()),
    )
    return result


def scrape_bbs_for_date(target_date: date) -> dict[str, dict[int, list[str]]]:
    """指定日の全レースの掲示板コメントを取得する。

    Returns:
        {race_id: {horse_num: [comment, ...]}} の辞書
    """
    race_ids = fetch_race_ids_for_date(target_date)
    if not race_ids:
        logger.info("%s: 開催なし", target_date)
        return {}

    logger.info("%s: %d races", target_date, len(race_ids))
    all_results: dict[str, dict[int, list[str]]] = {}

    for race_id in race_ids:
        all_results[race_id] = scrape_bbs_for_race(race_id)

    return all_results


def main() -> None:
    parser = argparse.ArgumentParser(description="netkeiba 掲示板取得")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="特定日 (YYYYMMDD)")
    group.add_argument("--race-id", dest="race_id", help="特定 race_id")

    parser.add_argument(
        "--hours-back",
        dest="hours_back",
        type=int,
        default=12,
        help="何時間前からのコメントを取得するか（デフォルト: 12）",
    )

    args = parser.parse_args()

    if args.date:
        d = date(int(args.date[:4]), int(args.date[4:6]), int(args.date[6:8]))
        results = scrape_bbs_for_date(d)
        # 結果のサマリーを表示
        for race_id, horse_comments in results.items():
            total = sum(len(v) for v in horse_comments.values())
            print(f"{race_id}: {len(horse_comments)} horses, {total} comments")
    elif args.race_id:
        results = scrape_bbs_for_race(args.race_id, hours_back=args.hours_back)
        for horse_num, comments in sorted(results.items()):
            print(f"#{horse_num}: {len(comments)} comments")
            for c in comments[:3]:  # 最初の3件を表示
                print(f"  - {c[:80]}")


if __name__ == "__main__":
    main()
