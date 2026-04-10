"""LightGBM モデル学習スクリプト

ウォークフォワード検証または全データ学習を行い、MLflow に結果を記録する。

実行例:
    # ウォークフォワード検証（2020〜2024年を1年ずつ検証）
    python model/train.py --walk-forward

    # 全データで本番モデルを学習・保存
    python model/train.py --train-final

    # 検証期間を指定
    python model/train.py --walk-forward --val-start 2022 --val-end 2024
"""

import argparse
import logging
from datetime import date, datetime

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 特徴量カラム定義
# ---------------------------------------------------------------------------
FEATURE_COLS = [
    # A. レース条件
    "distance",
    "course_type_enc",
    "direction_enc",
    "track_cond_enc",
    "race_class_rank",
    "num_horses",
    "venue_code",
    "weight_type_enc",
    "month",
    "gate_num",
    "horse_num",
    "burden_weight",
    "horse_weight",
    "weight_diff",
    # B. 馬の過去成績
    "career_runs",
    "career_win_rate",
    "career_place_rate",
    "career_avg_finish",
    "recent3_avg_finish",
    "recent5_avg_finish",
    "last_race_finish",
    "last_race_days",
    "last_race_class_rank",
    "avg_last3f_recent5",
    "is_first_race",
    "win_rate_same_dist",
    "place_rate_same_dist",
    "best_time_same_dist",
    "win_rate_same_course",
    "win_rate_same_venue",
    "win_rate_same_cond",
    # B2. 追加特徴量
    "popularity_rank",
    "burden_weight_diff",
    "prev_distance_diff",
    "prev_class_diff",
    # C. 騎手
    "jockey_win_rate_90d",
    "jockey_place_rate_90d",
    "jockey_win_rate_venue",
    "combo_runs",
    "jockey_horse_win_rate",
    # D. 調教師
    "trainer_win_rate_90d",
    "trainer_place_rate_90d",
    "trainer_win_rate_venue",
]

CATEGORICAL_COLS = [
    "course_type_enc",
    "direction_enc",
    "track_cond_enc",
    "venue_code",
    "weight_type_enc",
]

WIN_PARAMS: dict = {
    "objective": "binary",
    "metric": "auc",
    "learning_rate": 0.05,
    "num_leaves": 63,
    "scale_pos_weight": 13,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "min_child_samples": 50,
    "cat_smooth": 10,
    "verbosity": -1,
}

PLACE_PARAMS: dict = {
    **WIN_PARAMS,
    "scale_pos_weight": 4,
}

MODEL_DIR = "model/models"
DATA_START = date(2016, 1, 1)


def compute_sample_weights(df: pd.DataFrame, val_year: int) -> pd.Series:
    """直近データに重みを付ける（直近1年=2.0倍、直近2年=1.5倍）。"""
    weights = pd.Series(1.0, index=df.index)
    years = pd.to_datetime(df["held_date"]).dt.year
    weights[years >= val_year - 1] = 2.0
    weights[years == val_year - 2] = 1.5
    return weights


def _get_feature_cols(df: pd.DataFrame) -> list[str]:
    """df に存在する特徴量カラムのみ返す（未実装特徴量を除外）。"""
    return [c for c in FEATURE_COLS if c in df.columns]


def _coerce_feature_dtypes(df: pd.DataFrame, feat_cols: list[str]) -> pd.DataFrame:
    """LightGBM に渡す前に特徴量カラムの dtype を修正する。

    - venue_code: DB では text 型（"01" 等）のため int に変換
    - その他の特徴量: NULL 混じりで object になる場合があるため float に変換
    """
    df = df.copy()
    for col in feat_cols:
        if col in df.columns and df[col].dtype == object:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _train_model(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    params: dict,
    sample_weight: pd.Series | None,
    cat_cols: list[str],
):
    """LightGBM モデルを学習して返す。"""
    import lightgbm as lgb  # CI 互換のため関数内でインポート

    dtrain = lgb.Dataset(
        X_train,
        label=y_train,
        weight=sample_weight,
        categorical_feature=cat_cols,
        free_raw_data=False,
    )
    model = lgb.train(
        params,
        dtrain,
        num_boost_round=500,
        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(period=-1)],
        valid_sets=[dtrain],
    )
    return model


def compute_recovery_rate(
    df: pd.DataFrame,
    proba_col: str,
    odds_col: str,
    label_col: str,
    ev_threshold: float = 1.0,
) -> float:
    """EV > threshold で買い続けた場合の回収率（%）を計算する。

    注意: odds_col に事後情報（的中馬のみ非NULL）を使うとデータリークになる。
    単勝オッズ（win_odds）は全馬に存在するため問題ない。
    複勝は compute_place_recovery_rate を使うこと。
    """
    ev = df[proba_col] * df[odds_col]
    bets = df[ev > ev_threshold]
    if bets.empty:
        return float("nan")
    total_bet = len(bets)
    total_return = (bets[label_col] * bets[odds_col]).sum()
    return total_return / total_bet * 100


def compute_place_recovery_rate(
    df: pd.DataFrame,
    proba_col: str,
    label_col: str,
    place_odds_col: str = "place_odds",
    proba_threshold: float = 0.3,
) -> float:
    """複勝回収率を計算する。

    place_odds は実際に複勝圏に入った馬にしか設定されないため、
    EV フィルタは使えない（データリーク）。
    代わりに確率閾値で買い目を決め、実際の払戻で計算する。

    Args:
        proba_threshold: この確率以上の馬を買う（デフォルト 30%）
    """
    bets = df[df[proba_col] > proba_threshold].copy()
    if bets.empty:
        return float("nan")
    total_bet = len(bets)
    # 複勝圏に入った場合は place_odds 倍の払戻、圏外は 0
    returns = bets[label_col] * bets[place_odds_col].fillna(0)
    return returns.sum() / total_bet * 100


def walk_forward_validation(
    df: pd.DataFrame,
    val_start: int = 2020,
    val_end: int = 2024,
) -> dict:
    """ウォークフォワード検証を実行し、各ステップの指標を返す。"""
    results = []
    feat_cols = _get_feature_cols(df)
    cat_cols = [c for c in CATEGORICAL_COLS if c in feat_cols]

    df = _coerce_feature_dtypes(df, feat_cols)
    df["held_year"] = pd.to_datetime(df["held_date"]).dt.year

    for val_year in range(val_start, val_end + 1):
        train_df = df[df["held_year"] < val_year].copy()
        val_df = df[df["held_year"] == val_year].copy()

        if train_df.empty or val_df.empty:
            logger.warning("Skip year %d: insufficient data", val_year)
            continue

        # 取消・除外・着順なし行を除く
        train_df = train_df[train_df["win_label"].notna() & train_df["place_label"].notna()]
        val_df = val_df[val_df["win_label"].notna() & val_df["place_label"].notna()]

        X_train = train_df[feat_cols]
        X_val = val_df[feat_cols]
        weights = compute_sample_weights(train_df, val_year)

        logger.info(
            "Step val_year=%d | train=%d rows, val=%d rows",
            val_year,
            len(train_df),
            len(val_df),
        )

        # 単勝モデル
        win_model = _train_model(X_train, train_df["win_label"], WIN_PARAMS, weights, cat_cols)
        val_df["win_proba_raw"] = win_model.predict(X_val)

        # 複勝モデル
        place_model = _train_model(
            X_train, train_df["place_label"], PLACE_PARAMS, weights, cat_cols
        )
        val_df["place_proba_raw"] = place_model.predict(X_val)

        # レース内で確率を正規化
        val_df["win_proba"] = val_df.groupby("race_id")["win_proba_raw"].transform(
            lambda x: x / x.sum()
        )
        val_df["place_proba"] = val_df.groupby("race_id")["place_proba_raw"].transform(
            lambda x: x / x.sum()
        )

        # AUC
        from sklearn.metrics import roc_auc_score

        win_auc = roc_auc_score(val_df["win_label"], val_df["win_proba_raw"])
        place_auc = roc_auc_score(val_df["place_label"], val_df["place_proba_raw"])

        # 回収率（単勝: EV閾値、複勝: 確率閾値）
        win_recovery = compute_recovery_rate(val_df, "win_proba", "win_odds", "win_label")
        place_recovery = compute_place_recovery_rate(val_df, "place_proba", "place_label")

        step = {
            "val_year": val_year,
            "win_auc": win_auc,
            "place_auc": place_auc,
            "win_recovery": win_recovery,
            "place_recovery": place_recovery,
            "train_rows": len(train_df),
            "val_rows": len(val_df),
        }
        results.append(step)
        logger.info(
            "val_year=%d | win_AUC=%.4f place_AUC=%.4f | win_recovery=%.1f%% place_recovery=%.1f%%",
            val_year,
            win_auc,
            place_auc,
            win_recovery,
            place_recovery,
        )

        # MLflow に記録
        import mlflow  # noqa: PLC0415 (CI 互換のため関数内でインポート)

        with mlflow.start_run(run_name=f"wf_step_{val_year}", nested=True):
            mlflow.log_params(
                {
                    "val_year": val_year,
                    "train_rows": len(train_df),
                    "num_features": len(feat_cols),
                    "num_leaves": WIN_PARAMS["num_leaves"],
                    "learning_rate": WIN_PARAMS["learning_rate"],
                }
            )
            mlflow.log_metrics(
                {
                    "win_auc": win_auc,
                    "place_auc": place_auc,
                    "win_recovery": win_recovery,
                    "place_recovery": place_recovery,
                }
            )

    summary = {
        "steps": results,
        "mean_win_auc": np.mean([r["win_auc"] for r in results]),
        "mean_place_auc": np.mean([r["place_auc"] for r in results]),
        "mean_win_recovery": np.nanmean([r["win_recovery"] for r in results]),
        "mean_place_recovery": np.nanmean([r["place_recovery"] for r in results]),
    }
    return summary


def train_final_model(df: pd.DataFrame) -> tuple:
    """全データで本番モデルを学習し保存する。"""
    feat_cols = _get_feature_cols(df)
    cat_cols = [c for c in CATEGORICAL_COLS if c in feat_cols]

    df = _coerce_feature_dtypes(df, feat_cols)
    df = df[df["win_label"].notna() & df["place_label"].notna()].copy()
    df["held_year"] = pd.to_datetime(df["held_date"]).dt.year
    max_year = df["held_year"].max()
    weights = compute_sample_weights(df, max_year + 1)

    X = df[feat_cols]

    win_model = _train_model(X, df["win_label"], WIN_PARAMS, weights, cat_cols)
    place_model = _train_model(X, df["place_label"], PLACE_PARAMS, weights, cat_cols)

    yyyymm = datetime.now().strftime("%Y%m")
    win_path = f"{MODEL_DIR}/{yyyymm}_win.lgb"
    place_path = f"{MODEL_DIR}/{yyyymm}_place.lgb"
    win_model.save_model(win_path)
    place_model.save_model(place_path)
    logger.info("Saved: %s, %s", win_path, place_path)

    return win_model, place_model


def main() -> None:
    parser = argparse.ArgumentParser(description="LightGBM モデル学習")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--walk-forward", action="store_true", help="ウォークフォワード検証")
    group.add_argument(
        "--train-final", action="store_true", help="全データで本番モデルを学習・保存"
    )
    parser.add_argument(
        "--val-start", type=int, default=2020, help="検証開始年（デフォルト: 2020）"
    )
    parser.add_argument("--val-end", type=int, default=2024, help="検証終了年（デフォルト: 2024）")
    args = parser.parse_args()

    from features.feature_builder import build_training_dataset  # noqa: PLC0415

    logger.info("Loading training data from %s to today...", DATA_START)
    df = build_training_dataset(DATA_START, date.today())

    if df.empty:
        logger.error("No training data found. Run scrape_results first.")
        return

    if args.walk_forward:
        import mlflow  # noqa: PLC0415

        with mlflow.start_run(run_name="walk_forward"):
            summary = walk_forward_validation(df, args.val_start, args.val_end)
            mlflow.log_metrics(
                {
                    "mean_win_auc": summary["mean_win_auc"],
                    "mean_place_auc": summary["mean_place_auc"],
                    "mean_win_recovery": summary["mean_win_recovery"],
                    "mean_place_recovery": summary["mean_place_recovery"],
                }
            )
        print("\n=== ウォークフォワード検証結果 ===")
        for step in summary["steps"]:
            print(
                f"  {step['val_year']}: win_AUC={step['win_auc']:.4f} "
                f"place_AUC={step['place_auc']:.4f} "
                f"win_recovery={step['win_recovery']:.1f}% "
                f"place_recovery={step['place_recovery']:.1f}%"
            )
        print(f"\n平均 win_AUC={summary['mean_win_auc']:.4f}")
        print(f"平均 place_AUC={summary['mean_place_auc']:.4f}")
        print(f"平均 win_recovery={summary['mean_win_recovery']:.1f}%")
        print(f"平均 place_recovery={summary['mean_place_recovery']:.1f}%")

    elif args.train_final:
        train_final_model(df)


if __name__ == "__main__":
    main()
