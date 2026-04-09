"""推論・期待値計算スクリプト

保存済みモデルを使って当日レースの予測確率・期待値を計算し、
predictions テーブルに保存する。

実行例:
    python model/predict.py --race-id 202606030401
    python model/predict.py --date 20260606
"""

import argparse
import glob
import logging
from datetime import date, datetime

import pandas as pd

from model.train import FEATURE_COLS, MODEL_DIR
from scraper.db import get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def load_latest_model(model_type: str):
    """最新の .lgb モデルファイルを読み込む。"""
    import lightgbm as lgb  # CI 互換のため関数内でインポート

    pattern = f"{MODEL_DIR}/*_{model_type}.lgb"
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(f"No model found: {pattern}")
    model_path = files[-1]
    logger.info("Loading model: %s", model_path)
    return lgb.Booster(model_file=model_path)


def predict_race(race_id: str) -> pd.DataFrame | None:
    """1レース分の予測確率・期待値を計算して返す。"""
    from features.feature_builder import build_inference_features  # noqa: PLC0415

    df = build_inference_features(race_id)
    if df.empty:
        logger.warning("No features for race %s", race_id)
        return None

    feat_cols = [c for c in FEATURE_COLS if c in df.columns]

    win_model = load_latest_model("win")
    place_model = load_latest_model("place")

    X = df[feat_cols]
    df["win_proba_raw"] = win_model.predict(X)
    df["place_proba_raw"] = place_model.predict(X)

    # レース内で確率を正規化（合計=1）
    win_sum = df["win_proba_raw"].sum()
    place_sum = df["place_proba_raw"].sum()
    df["win_proba"] = df["win_proba_raw"] / win_sum if win_sum > 0 else df["win_proba_raw"]
    df["place_proba"] = (
        df["place_proba_raw"] / place_sum if place_sum > 0 else df["place_proba_raw"]
    )

    # オッズを odds テーブルから取得（直近スナップショット）
    df = _attach_latest_odds(df, race_id)

    # 期待値計算
    df["win_ev"] = df["win_proba"] * df["win_odds_latest"].fillna(0)
    df["place_ev"] = df["place_proba"] * df["place_odds_latest"].fillna(0)

    return df[["race_id", "horse_num", "win_proba", "place_proba", "win_ev", "place_ev"]]


def _attach_latest_odds(df: pd.DataFrame, race_id: str) -> pd.DataFrame:
    """odds テーブルから直近の単勝・複勝オッズを取得してマージする。"""
    sql = """
    SELECT
        horse_num,
        odds_type,
        odds_low
    FROM odds
    WHERE race_id = %(race_id)s
      AND fetched_at = (
          SELECT MAX(fetched_at) FROM odds WHERE race_id = %(race_id)s
      )
    """
    try:
        with get_conn() as conn:
            import psycopg2.extras

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, {"race_id": race_id})
                rows = cur.fetchall()
        odds_df = pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()

        if odds_df.empty:
            df["win_odds_latest"] = None
            df["place_odds_latest"] = None
            return df

        win_odds = (
            odds_df[odds_df["odds_type"] == "win"]
            .set_index("horse_num")["odds_low"]
            .rename("win_odds_latest")
        )
        place_odds = (
            odds_df[odds_df["odds_type"] == "place"]
            .set_index("horse_num")["odds_low"]
            .rename("place_odds_latest")
        )
        df = df.join(win_odds, on="horse_num").join(place_odds, on="horse_num")
    except Exception as e:
        logger.warning("Failed to fetch odds for %s: %s", race_id, e)
        df["win_odds_latest"] = None
        df["place_odds_latest"] = None

    return df


def save_predictions(pred_df: pd.DataFrame) -> None:
    """predictions テーブルに保存する（既存行は上書き）。"""
    if pred_df is None or pred_df.empty:
        return

    predicted_at = datetime.now()
    sql = """
    INSERT INTO predictions (race_id, horse_num, predicted_at, win_proba, place_proba, win_ev, place_ev)
    VALUES (%(race_id)s, %(horse_num)s, %(predicted_at)s, %(win_proba)s, %(place_proba)s,
            %(win_ev)s, %(place_ev)s)
    ON CONFLICT (race_id, horse_num, predicted_at) DO UPDATE SET
        win_proba   = EXCLUDED.win_proba,
        place_proba = EXCLUDED.place_proba,
        win_ev      = EXCLUDED.win_ev,
        place_ev    = EXCLUDED.place_ev
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            for _, row in pred_df.iterrows():
                cur.execute(
                    sql,
                    {
                        "race_id": row["race_id"],
                        "horse_num": int(row["horse_num"]),
                        "predicted_at": predicted_at,
                        "win_proba": float(row["win_proba"]),
                        "place_proba": float(row["place_proba"]),
                        "win_ev": float(row["win_ev"]) if pd.notna(row["win_ev"]) else None,
                        "place_ev": float(row["place_ev"]) if pd.notna(row["place_ev"]) else None,
                    },
                )
    logger.info("Saved %d predictions for %s", len(pred_df), pred_df["race_id"].iloc[0])


def predict_date(target_date: date) -> None:
    """指定日の全レースを予測する。"""
    from scraper.scrape_calendar import fetch_race_ids_for_date

    race_ids = fetch_race_ids_for_date(target_date)
    if not race_ids:
        logger.info("No races on %s", target_date)
        return

    for race_id in race_ids:
        try:
            pred_df = predict_race(race_id)
            save_predictions(pred_df)
        except Exception as e:
            logger.error("Error predicting %s: %s", race_id, e)


def main() -> None:
    parser = argparse.ArgumentParser(description="レース予測・期待値計算")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--race-id", help="特定レースID（例: 202606030401）")
    group.add_argument("--date", help="特定日の全レース（YYYYMMDD）")
    args = parser.parse_args()

    if args.race_id:
        pred_df = predict_race(args.race_id)
        if pred_df is not None:
            save_predictions(pred_df)
            print(
                pred_df[["horse_num", "win_proba", "place_proba", "win_ev", "place_ev"]].to_string()
            )
    elif args.date:
        d = date(int(args.date[:4]), int(args.date[4:6]), int(args.date[6:8]))
        predict_date(d)


if __name__ == "__main__":
    main()
