"""特徴量生成モジュール

DBに蓄積されたレース結果データから、LightGBM に投入する特徴量 DataFrame を生成する。

使用例:
    from datetime import date
    from features.feature_builder import build_training_dataset, build_inference_features

    # 学習用データセット生成
    df = build_training_dataset(date(2020, 1, 1), date(2025, 12, 31))

    # 推論用特徴量生成（当日出馬表）
    df = build_inference_features("202606030401")
"""

import logging
from datetime import date

import pandas as pd
import psycopg2
import psycopg2.extras

from scraper.db import get_conn

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# クラスランクのマッピング
# ---------------------------------------------------------------------------
RACE_CLASS_RANK: dict[str, int] = {
    "新馬": 0,
    "未勝利": 1,
    "1勝クラス": 2,
    "2勝クラス": 3,
    "3勝クラス": 4,
    "オープン": 5,
    "G3": 6,
    "G2": 7,
    "G1": 8,
}

COURSE_TYPE_MAP = {"芝": 0, "ダート": 1}
DIRECTION_MAP = {"右": 0, "左": 1, "直線": 2}
TRACK_COND_MAP = {"良": 0, "稍重": 1, "重": 2, "不良": 3}
WEIGHT_TYPE_MAP = {"馬齢": 0, "ハンデ": 1, "別定": 2}


def _map_race_class(race_class: str | None) -> int | None:
    """race_class 文字列をランク数値に変換する。部分一致でマッチング。"""
    if race_class is None:
        return None
    for key, rank in RACE_CLASS_RANK.items():
        if key in race_class:
            return rank
    return None


def _parse_last_corner_position(passing_order: str | None) -> int | None:
    """通過順文字列（例: "3-2-2-1"）から最終コーナー通過順を返す。"""
    if not passing_order:
        return None
    parts = passing_order.split("-")
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# SQL: レース条件 + エントリー情報
# ---------------------------------------------------------------------------
_RACE_ENTRY_SQL = """
SELECT
    r.race_id,
    r.held_date,
    r.venue_code,
    r.distance,
    r.course_type,
    r.direction,
    r.track_cond,
    r.weather,
    r.race_class,
    r.num_horses,
    r.weight_type,
    e.horse_num,
    e.horse_id,
    e.jockey_id,
    e.trainer_id,
    e.gate_num,
    e.burden_weight,
    e.horse_weight,
    e.weight_diff
FROM races r
JOIN entries e ON r.race_id = e.race_id
WHERE r.held_date BETWEEN %(start_date)s AND %(end_date)s
  AND (e.scratch IS NULL OR e.scratch = FALSE)
ORDER BY r.held_date, r.race_id, e.horse_num
"""

_RACE_ENTRY_BY_RACE_SQL = """
SELECT
    r.race_id,
    r.held_date,
    r.venue_code,
    r.distance,
    r.course_type,
    r.direction,
    r.track_cond,
    r.weather,
    r.race_class,
    r.num_horses,
    r.weight_type,
    e.horse_num,
    e.horse_id,
    e.jockey_id,
    e.trainer_id,
    e.gate_num,
    e.burden_weight,
    e.horse_weight,
    e.weight_diff
FROM races r
JOIN entries e ON r.race_id = e.race_id
WHERE r.race_id = %(race_id)s
  AND (e.scratch IS NULL OR e.scratch = FALSE)
ORDER BY e.horse_num
"""

# ---------------------------------------------------------------------------
# SQL: ラベル（results）
# ---------------------------------------------------------------------------
_LABELS_SQL = """
SELECT
    race_id,
    horse_num,
    finish_pos,
    win_odds,
    place_odds,
    popularity,
    last_3f,
    passing_order,
    time_sec
FROM results
WHERE race_id = ANY(%(race_ids)s)
"""

# ---------------------------------------------------------------------------
# SQL: 馬の過去成績（データリーク防止: held_date < current）
# ---------------------------------------------------------------------------
_HORSE_PAST_SQL = """
WITH horse_races AS (
    SELECT
        e.horse_id,
        rc.held_date,
        rc.race_id,
        rc.distance,
        rc.course_type,
        rc.venue_code,
        rc.track_cond,
        rc.race_class,
        r.finish_pos,
        r.last_3f,
        r.passing_order,
        r.time_sec,
        ROW_NUMBER() OVER (PARTITION BY e.horse_id ORDER BY rc.held_date DESC) AS rn
    FROM entries e
    JOIN results r ON e.race_id = r.race_id AND e.horse_num = r.horse_num
    JOIN races rc ON e.race_id = rc.race_id
    WHERE e.horse_id = ANY(%(horse_ids)s)
      AND rc.held_date < %(current_date)s
      AND r.finish_pos IS NOT NULL
)
SELECT
    horse_id,
    COUNT(*)                                                              AS career_runs,
    SUM(CASE WHEN finish_pos = 1 THEN 1 ELSE 0 END)                     AS career_wins,
    SUM(CASE WHEN finish_pos <= 3 THEN 1 ELSE 0 END)                    AS career_places,
    AVG(finish_pos)                                                       AS career_avg_finish,
    -- 直近3走
    AVG(CASE WHEN rn <= 3 THEN finish_pos END)                           AS recent3_avg_finish,
    -- 直近5走
    AVG(CASE WHEN rn <= 5 THEN finish_pos END)                           AS recent5_avg_finish,
    -- 前走
    MAX(CASE WHEN rn = 1 THEN finish_pos END)                            AS last_race_finish,
    MAX(CASE WHEN rn = 1 THEN held_date END)                             AS last_race_date,
    MAX(CASE WHEN rn = 1 THEN race_class END)                            AS last_race_class,
    MAX(CASE WHEN rn = 1 THEN distance END)                              AS last_race_distance,
    -- 直近5走の上がり3F平均
    AVG(CASE WHEN rn <= 5 AND last_3f IS NOT NULL THEN last_3f END)     AS avg_last3f_recent5
FROM horse_races
GROUP BY horse_id
"""

_HORSE_COND_SQL = """
WITH horse_races AS (
    SELECT
        e.horse_id,
        rc.distance,
        rc.course_type,
        rc.venue_code,
        rc.track_cond,
        r.finish_pos,
        r.time_sec
    FROM entries e
    JOIN results r ON e.race_id = r.race_id AND e.horse_num = r.horse_num
    JOIN races rc ON e.race_id = rc.race_id
    WHERE e.horse_id = ANY(%(horse_ids)s)
      AND rc.held_date < %(current_date)s
      AND r.finish_pos IS NOT NULL
)
SELECT
    horse_id,
    -- 同距離
    %(target_distance)s                                                                AS target_distance,
    SUM(CASE WHEN distance = %(target_distance)s THEN 1 ELSE 0 END)                  AS runs_same_dist,
    SUM(CASE WHEN distance = %(target_distance)s AND finish_pos = 1 THEN 1 ELSE 0 END) AS wins_same_dist,
    SUM(CASE WHEN distance = %(target_distance)s AND finish_pos <= 3 THEN 1 ELSE 0 END) AS places_same_dist,
    MIN(CASE WHEN distance = %(target_distance)s AND time_sec IS NOT NULL THEN time_sec END) AS best_time_same_dist,
    -- 同コース種別
    %(target_course)s                                                                  AS target_course,
    SUM(CASE WHEN course_type = %(target_course)s THEN 1 ELSE 0 END)                 AS runs_same_course,
    SUM(CASE WHEN course_type = %(target_course)s AND finish_pos = 1 THEN 1 ELSE 0 END) AS wins_same_course,
    -- 同競馬場
    %(target_venue)s                                                                   AS target_venue,
    SUM(CASE WHEN venue_code = %(target_venue)s THEN 1 ELSE 0 END)                   AS runs_same_venue,
    SUM(CASE WHEN venue_code = %(target_venue)s AND finish_pos = 1 THEN 1 ELSE 0 END) AS wins_same_venue,
    -- 同馬場状態
    %(target_cond)s                                                                    AS target_cond,
    SUM(CASE WHEN track_cond = %(target_cond)s THEN 1 ELSE 0 END)                    AS runs_same_cond,
    SUM(CASE WHEN track_cond = %(target_cond)s AND finish_pos = 1 THEN 1 ELSE 0 END) AS wins_same_cond
FROM horse_races
GROUP BY horse_id
"""

# ---------------------------------------------------------------------------
# SQL: 騎手の過去成績（直近90日）
# ---------------------------------------------------------------------------
_JOCKEY_STATS_SQL = """
WITH jockey_races AS (
    SELECT
        e.jockey_id,
        e.horse_id,
        rc.held_date,
        rc.venue_code,
        r.finish_pos
    FROM entries e
    JOIN results r ON e.race_id = r.race_id AND e.horse_num = r.horse_num
    JOIN races rc ON e.race_id = rc.race_id
    WHERE e.jockey_id = ANY(%(jockey_ids)s)
      AND rc.held_date < %(current_date)s
      AND rc.held_date >= %(cutoff_date)s
      AND r.finish_pos IS NOT NULL
)
SELECT
    jockey_id,
    COUNT(*)                                                              AS jockey_runs_90d,
    SUM(CASE WHEN finish_pos = 1 THEN 1 ELSE 0 END)                     AS jockey_wins_90d,
    SUM(CASE WHEN finish_pos <= 3 THEN 1 ELSE 0 END)                    AS jockey_places_90d,
    SUM(CASE WHEN venue_code = %(target_venue)s THEN 1 ELSE 0 END)      AS jockey_runs_venue,
    SUM(CASE WHEN venue_code = %(target_venue)s AND finish_pos = 1 THEN 1 ELSE 0 END) AS jockey_wins_venue
FROM jockey_races
GROUP BY jockey_id
"""

# ---------------------------------------------------------------------------
# SQL: 騎手×馬のコンビ成績
# ---------------------------------------------------------------------------
_JOCKEY_HORSE_SQL = """
SELECT
    e.jockey_id,
    e.horse_id,
    COUNT(*)                                                              AS combo_runs,
    SUM(CASE WHEN r.finish_pos = 1 THEN 1 ELSE 0 END)                   AS combo_wins
FROM entries e
JOIN results r ON e.race_id = r.race_id AND e.horse_num = r.horse_num
JOIN races rc ON e.race_id = rc.race_id
WHERE (e.jockey_id, e.horse_id) IN (
    SELECT unnest(%(jockey_ids)s::text[]), unnest(%(horse_ids)s::text[])
)
  AND rc.held_date < %(current_date)s
  AND r.finish_pos IS NOT NULL
GROUP BY e.jockey_id, e.horse_id
"""

# ---------------------------------------------------------------------------
# SQL: 調教師の過去成績（直近90日）
# ---------------------------------------------------------------------------
_TRAINER_STATS_SQL = """
WITH trainer_races AS (
    SELECT
        e.trainer_id,
        rc.venue_code,
        r.finish_pos
    FROM entries e
    JOIN results r ON e.race_id = r.race_id AND e.horse_num = r.horse_num
    JOIN races rc ON e.race_id = rc.race_id
    WHERE e.trainer_id = ANY(%(trainer_ids)s)
      AND rc.held_date < %(current_date)s
      AND rc.held_date >= %(cutoff_date)s
      AND r.finish_pos IS NOT NULL
)
SELECT
    trainer_id,
    COUNT(*)                                                              AS trainer_runs_90d,
    SUM(CASE WHEN finish_pos = 1 THEN 1 ELSE 0 END)                     AS trainer_wins_90d,
    SUM(CASE WHEN finish_pos <= 3 THEN 1 ELSE 0 END)                    AS trainer_places_90d,
    SUM(CASE WHEN venue_code = %(target_venue)s THEN 1 ELSE 0 END)      AS trainer_runs_venue,
    SUM(CASE WHEN venue_code = %(target_venue)s AND finish_pos = 1 THEN 1 ELSE 0 END) AS trainer_wins_venue
FROM trainer_races
GROUP BY trainer_id
"""


def _fetch_df(conn, sql: str, params: dict) -> pd.DataFrame:
    """SQL を実行して DataFrame を返す。"""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()


def _build_features_for_batch(
    conn,
    base_df: pd.DataFrame,
    labels_df: pd.DataFrame | None,
) -> pd.DataFrame:
    """base_df（レース条件＋エントリー）に特徴量を付加して返す。"""

    if base_df.empty:
        return base_df

    # --- エンコーディング ---
    base_df["course_type_enc"] = base_df["course_type"].map(COURSE_TYPE_MAP)
    base_df["direction_enc"] = base_df["direction"].map(DIRECTION_MAP)
    base_df["track_cond_enc"] = base_df["track_cond"].map(TRACK_COND_MAP)
    base_df["weight_type_enc"] = base_df["weight_type"].map(WEIGHT_TYPE_MAP)
    base_df["race_class_rank"] = base_df["race_class"].apply(_map_race_class)
    base_df["month"] = pd.to_datetime(base_df["held_date"]).dt.month
    avg_burden = base_df.groupby("race_id")["burden_weight"].transform("mean")
    base_df["burden_weight_diff"] = base_df["burden_weight"] - avg_burden

    # --- 馬の過去成績（レースごとにグループ化して一括取得）---
    # レースごとに current_date が異なるため、日付でグループ化
    horse_past_frames = []
    horse_cond_frames = []

    for (held_date, venue, distance, course_type, track_cond), group in base_df.groupby(
        ["held_date", "venue_code", "distance", "course_type", "track_cond"]
    ):
        horse_ids = group["horse_id"].dropna().unique().tolist()
        if not horse_ids:
            continue

        from datetime import timedelta

        cutoff_90d = held_date - timedelta(days=90)

        # 通算成績
        past_df = _fetch_df(
            conn,
            _HORSE_PAST_SQL,
            {"horse_ids": horse_ids, "current_date": held_date},
        )
        if not past_df.empty:
            past_df["_held_date"] = held_date
            horse_past_frames.append(past_df)

        # 条件別成績
        cond_df = _fetch_df(
            conn,
            _HORSE_COND_SQL,
            {
                "horse_ids": horse_ids,
                "current_date": held_date,
                "target_distance": int(distance),
                "target_course": course_type,
                "target_venue": venue,
                "target_cond": track_cond,
            },
        )
        if not cond_df.empty:
            cond_df["_held_date"] = held_date
            horse_cond_frames.append(cond_df)

    # 全レースの過去成績をマージ
    if horse_past_frames:
        past_all = pd.concat(horse_past_frames, ignore_index=True)
        past_all["career_win_rate"] = past_all["career_wins"] / past_all["career_runs"].replace(
            0, float("nan")
        )
        past_all["career_place_rate"] = past_all["career_places"] / past_all["career_runs"].replace(
            0, float("nan")
        )
        past_all["is_first_race"] = (past_all["career_runs"] == 0).astype(int)
        past_all["last_race_class_rank"] = past_all["last_race_class"].apply(_map_race_class)
        past_all["last_race_days"] = (
            pd.to_datetime(
                base_df.set_index(["horse_id", "held_date"])
                .index.get_level_values("held_date")
                .unique()
                .min()
            )
            - pd.to_datetime(past_all["last_race_date"])
        ).dt.days.abs()

        base_df = base_df.merge(
            past_all.drop(columns=["last_race_class"]),
            left_on=["horse_id", "held_date"],
            right_on=["horse_id", "_held_date"],
            how="left",
        ).drop(columns=["_held_date"], errors="ignore")

        base_df["prev_distance_diff"] = base_df["distance"] - base_df["last_race_distance"]
        base_df["prev_class_diff"] = base_df["race_class_rank"] - base_df["last_race_class_rank"]
    else:
        for col in [
            "career_runs",
            "career_wins",
            "career_places",
            "career_avg_finish",
            "career_win_rate",
            "career_place_rate",
            "recent3_avg_finish",
            "recent5_avg_finish",
            "last_race_finish",
            "last_race_days",
            "last_race_class_rank",
            "avg_last3f_recent5",
            "is_first_race",
            "prev_class_diff",
            "prev_distance_diff",
        ]:
            base_df[col] = float("nan")

    if horse_cond_frames:
        cond_all = pd.concat(horse_cond_frames, ignore_index=True)
        cond_all["win_rate_same_dist"] = cond_all["wins_same_dist"] / cond_all[
            "runs_same_dist"
        ].replace(0, float("nan"))
        cond_all["place_rate_same_dist"] = cond_all["places_same_dist"] / cond_all[
            "runs_same_dist"
        ].replace(0, float("nan"))
        cond_all["win_rate_same_course"] = cond_all["wins_same_course"] / cond_all[
            "runs_same_course"
        ].replace(0, float("nan"))
        cond_all["win_rate_same_venue"] = cond_all["wins_same_venue"] / cond_all[
            "runs_same_venue"
        ].replace(0, float("nan"))
        cond_all["win_rate_same_cond"] = cond_all["wins_same_cond"] / cond_all[
            "runs_same_cond"
        ].replace(0, float("nan"))

        base_df = base_df.merge(
            cond_all[
                [
                    "horse_id",
                    "_held_date",
                    "best_time_same_dist",
                    "win_rate_same_dist",
                    "place_rate_same_dist",
                    "win_rate_same_course",
                    "win_rate_same_venue",
                    "win_rate_same_cond",
                ]
            ],
            left_on=["horse_id", "held_date"],
            right_on=["horse_id", "_held_date"],
            how="left",
        ).drop(columns=["_held_date"], errors="ignore")

    # --- 騎手・調教師の成績（レースごと）---
    jockey_frames = []
    trainer_frames = []

    for (held_date, venue), group in base_df.groupby(["held_date", "venue_code"]):
        from datetime import timedelta

        cutoff_90d = held_date - timedelta(days=90)
        jockey_ids = group["jockey_id"].dropna().unique().tolist()
        trainer_ids = group["trainer_id"].dropna().unique().tolist()

        if jockey_ids:
            j_df = _fetch_df(
                conn,
                _JOCKEY_STATS_SQL,
                {
                    "jockey_ids": jockey_ids,
                    "current_date": held_date,
                    "cutoff_date": cutoff_90d,
                    "target_venue": venue,
                },
            )
            if not j_df.empty:
                j_df["_held_date"] = held_date
                jockey_frames.append(j_df)

        if trainer_ids:
            t_df = _fetch_df(
                conn,
                _TRAINER_STATS_SQL,
                {
                    "trainer_ids": trainer_ids,
                    "current_date": held_date,
                    "cutoff_date": cutoff_90d,
                    "target_venue": venue,
                },
            )
            if not t_df.empty:
                t_df["_held_date"] = held_date
                trainer_frames.append(t_df)

    if jockey_frames:
        j_all = pd.concat(jockey_frames, ignore_index=True)
        j_all["jockey_win_rate_90d"] = j_all["jockey_wins_90d"] / j_all["jockey_runs_90d"].replace(
            0, float("nan")
        )
        j_all["jockey_place_rate_90d"] = j_all["jockey_places_90d"] / j_all[
            "jockey_runs_90d"
        ].replace(0, float("nan"))
        j_all["jockey_win_rate_venue"] = j_all["jockey_wins_venue"] / j_all[
            "jockey_runs_venue"
        ].replace(0, float("nan"))
        base_df = base_df.merge(
            j_all[
                [
                    "jockey_id",
                    "_held_date",
                    "jockey_win_rate_90d",
                    "jockey_place_rate_90d",
                    "jockey_win_rate_venue",
                ]
            ],
            left_on=["jockey_id", "held_date"],
            right_on=["jockey_id", "_held_date"],
            how="left",
        ).drop(columns=["_held_date"], errors="ignore")

    if trainer_frames:
        t_all = pd.concat(trainer_frames, ignore_index=True)
        t_all["trainer_win_rate_90d"] = t_all["trainer_wins_90d"] / t_all[
            "trainer_runs_90d"
        ].replace(0, float("nan"))
        t_all["trainer_place_rate_90d"] = t_all["trainer_places_90d"] / t_all[
            "trainer_runs_90d"
        ].replace(0, float("nan"))
        t_all["trainer_win_rate_venue"] = t_all["trainer_wins_venue"] / t_all[
            "trainer_runs_venue"
        ].replace(0, float("nan"))
        base_df = base_df.merge(
            t_all[
                [
                    "trainer_id",
                    "_held_date",
                    "trainer_win_rate_90d",
                    "trainer_place_rate_90d",
                    "trainer_win_rate_venue",
                ]
            ],
            left_on=["trainer_id", "held_date"],
            right_on=["trainer_id", "_held_date"],
            how="left",
        ).drop(columns=["_held_date"], errors="ignore")

    # --- 騎手×馬コンビ成績 ---
    # 全ペアを一括で取得するため unnest を使用
    # (jockey_id, horse_id) の組み合わせを渡す
    combos = base_df[["jockey_id", "horse_id", "held_date"]].dropna(
        subset=["jockey_id", "horse_id"]
    )
    if not combos.empty:
        # 日付ごとに分けて取得
        combo_frames = []
        for held_date, grp in combos.groupby("held_date"):
            j_ids = grp["jockey_id"].tolist()
            h_ids = grp["horse_id"].tolist()
            if j_ids:
                combo_df = _fetch_df(
                    conn,
                    _JOCKEY_HORSE_SQL,
                    {
                        "jockey_ids": j_ids,
                        "horse_ids": h_ids,
                        "current_date": held_date,
                    },
                )
                if not combo_df.empty:
                    combo_df["_held_date"] = held_date
                    combo_frames.append(combo_df)

        if combo_frames:
            combo_all = pd.concat(combo_frames, ignore_index=True)
            combo_all["jockey_horse_win_rate"] = combo_all["combo_wins"] / combo_all[
                "combo_runs"
            ].replace(0, float("nan"))
            base_df = base_df.merge(
                combo_all[
                    ["jockey_id", "horse_id", "_held_date", "combo_runs", "jockey_horse_win_rate"]
                ],
                left_on=["jockey_id", "horse_id", "held_date"],
                right_on=["jockey_id", "horse_id", "_held_date"],
                how="left",
            ).drop(columns=["_held_date"], errors="ignore")

    # --- ラベルのマージ（学習時のみ）---
    if labels_df is not None and not labels_df.empty:
        base_df = base_df.merge(
            labels_df[
                [
                    "race_id",
                    "horse_num",
                    "finish_pos",
                    "win_odds",
                    "place_odds",
                    "popularity",
                    "last_3f",
                    "passing_order",
                    "time_sec",
                ]
            ],
            on=["race_id", "horse_num"],
            how="left",
        )
        base_df["win_label"] = (base_df["finish_pos"] == 1).astype(int)
        base_df["place_label"] = (base_df["finish_pos"] <= 3).astype(int)
        base_df["popularity_rank"] = base_df["popularity"]

    return base_df


def build_training_dataset(start_date: date, end_date: date) -> pd.DataFrame:
    """期間内の全レース・全馬の特徴量 + ラベルを含む DataFrame を返す。

    1行 = 1頭 × 1レース（取消・除外除く）。
    """
    logger.info("Building training dataset from %s to %s", start_date, end_date)

    with get_conn() as conn:
        # レース条件＋エントリー
        base_df = _fetch_df(conn, _RACE_ENTRY_SQL, {"start_date": start_date, "end_date": end_date})

        if base_df.empty:
            logger.warning("No data found for %s - %s", start_date, end_date)
            return base_df

        # ラベル（results）
        race_ids = base_df["race_id"].unique().tolist()
        labels_df = _fetch_df(conn, _LABELS_SQL, {"race_ids": race_ids})

        df = _build_features_for_batch(conn, base_df, labels_df)

    logger.info("Training dataset: %d rows, %d columns", len(df), len(df.columns))
    return df


def build_inference_features(race_id: str) -> pd.DataFrame:
    """当日レースの出馬表から特徴量 DataFrame を返す（ラベルなし）。"""
    logger.info("Building inference features for race %s", race_id)

    with get_conn() as conn:
        base_df = _fetch_df(conn, _RACE_ENTRY_BY_RACE_SQL, {"race_id": race_id})

        if base_df.empty:
            logger.warning("No entries found for race %s", race_id)
            return base_df

        df = _build_features_for_batch(conn, base_df, labels_df=None)

    logger.info("Inference features: %d horses, %d columns", len(df), len(df.columns))
    return df
