"""騎手・調教師のプロフィール取得スクリプト

netkeiba のプロフィールページから所属（belong_to）を取得し、
jockeys / trainers テーブルを更新する。

初回バッチ（scrape_results）完了後に一括で実行する。

実行例:
    # 全員（騎手＋調教師）
    python scraper/scrape_person_profiles.py --all

    # 騎手のみ
    python scraper/scrape_person_profiles.py --jockeys

    # 調教師のみ
    python scraper/scrape_person_profiles.py --trainers
"""

import argparse
import logging
import re

from scraper.db import (
    get_conn,
    get_jockeys_without_belong_to,
    get_trainers_without_belong_to,
    update_jockey_belong_to,
    update_trainer_belong_to,
)
from scraper.utils import fetch_html, parse_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

JOCKEY_URL = "https://db.netkeiba.com/jockey/{jockey_id}/"
TRAINER_URL = "https://db.netkeiba.com/trainer/{trainer_id}/"

# 所属として認識するキーワード（優先順位順）
KNOWN_LOCATIONS = ["美浦", "栗東", "地方", "海外"]


def parse_belong_to(soup) -> str | None:
    """プロフィールページから所属を抽出する。

    騎手ページ: `[美浦]` 形式（角括弧付き）
    調教師ページ: `美浦` 形式（テキストノード）

    どちらにも対応するため全文検索でキーワードを探す。
    """
    p = soup.select_one("p.txt_01")
    if not p:
        return None

    text = p.get_text()

    # 角括弧形式: [美浦]
    m = re.search(r"\[(.+?)\]", text)
    if m:
        return m.group(1)

    # プレーンテキスト形式
    for loc in KNOWN_LOCATIONS:
        if loc in text:
            return loc

    return None


def scrape_jockeys() -> None:
    """belong_to が NULL の全騎手の所属を取得して更新する。"""
    with get_conn() as conn:
        jockeys = get_jockeys_without_belong_to(conn)

    if not jockeys:
        logger.info("All jockeys already have belong_to set.")
        return

    logger.info("Fetching belong_to for %d jockeys...", len(jockeys))
    updated = 0
    failed = 0

    for jockey_id, jockey_name in jockeys:
        try:
            url = JOCKEY_URL.format(jockey_id=jockey_id)
            html = fetch_html(url)
            soup = parse_html(html)
            belong_to = parse_belong_to(soup)

            if belong_to:
                with get_conn() as conn:
                    update_jockey_belong_to(conn, jockey_id, belong_to)
                logger.debug("Jockey %s (%s): %s", jockey_id, jockey_name, belong_to)
                updated += 1
            else:
                logger.warning(
                    "Could not parse belong_to for jockey %s (%s)", jockey_id, jockey_name
                )
                failed += 1
        except Exception as e:
            logger.error("Error fetching jockey %s: %s", jockey_id, e)
            failed += 1

    logger.info("Jockeys done: %d updated, %d failed", updated, failed)


def scrape_trainers() -> None:
    """belong_to が NULL の全調教師の所属を取得して更新する。"""
    with get_conn() as conn:
        trainers = get_trainers_without_belong_to(conn)

    if not trainers:
        logger.info("All trainers already have belong_to set.")
        return

    logger.info("Fetching belong_to for %d trainers...", len(trainers))
    updated = 0
    failed = 0

    for trainer_id, trainer_name in trainers:
        try:
            url = TRAINER_URL.format(trainer_id=trainer_id)
            html = fetch_html(url)
            soup = parse_html(html)
            belong_to = parse_belong_to(soup)

            if belong_to:
                with get_conn() as conn:
                    update_trainer_belong_to(conn, trainer_id, belong_to)
                logger.debug("Trainer %s (%s): %s", trainer_id, trainer_name, belong_to)
                updated += 1
            else:
                logger.warning(
                    "Could not parse belong_to for trainer %s (%s)", trainer_id, trainer_name
                )
                failed += 1
        except Exception as e:
            logger.error("Error fetching trainer %s: %s", trainer_id, e)
            failed += 1

    logger.info("Trainers done: %d updated, %d failed", updated, failed)


def main() -> None:
    parser = argparse.ArgumentParser(description="騎手・調教師の所属取得")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="騎手・調教師の両方を処理")
    group.add_argument("--jockeys", action="store_true", help="騎手のみ処理")
    group.add_argument("--trainers", action="store_true", help="調教師のみ処理")

    args = parser.parse_args()

    if args.jockeys or args.all:
        scrape_jockeys()
    if args.trainers or args.all:
        scrape_trainers()


if __name__ == "__main__":
    main()
