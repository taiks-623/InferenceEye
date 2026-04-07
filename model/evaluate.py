"""モデル評価スクリプト

回収率シミュレーション・特徴量重要度の可視化を行う。

実行例:
    # 2024年の回収率シミュレーション
    python model/evaluate.py --year 2024

    # 特徴量重要度プロット
    python model/evaluate.py --feature-importance
"""

import argparse
import logging
from datetime import date

import pandas as pd

from model.predict import load_latest_model
from model.train import FEATURE_COLS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def simulate_recovery(df: pd.DataFrame, ev_threshold: float = 1.0) -> dict:
    """EV > threshold で買い続けた場合の回収率シミュレーション。"""
    feat_cols = [c for c in FEATURE_COLS if c in df.columns]
    df = df[df["win_label"].notna() & df["place_label"].notna()].copy()

    win_model = load_latest_model("win")
    place_model = load_latest_model("place")

    df["win_proba_raw"] = win_model.predict(df[feat_cols])
    df["place_proba_raw"] = place_model.predict(df[feat_cols])

    df["win_proba"] = df.groupby("race_id")["win_proba_raw"].transform(lambda x: x / x.sum())
    df["place_proba"] = df.groupby("race_id")["place_proba_raw"].transform(lambda x: x / x.sum())

    win_ev = df["win_proba"] * df["win_odds"].fillna(0)
    place_ev = df["place_proba"] * df["win_odds"].fillna(0)

    win_bets = df[win_ev > ev_threshold]
    place_bets = df[place_ev > ev_threshold]

    def _recovery(bets: pd.DataFrame, label_col: str, odds_col: str) -> dict:
        if bets.empty:
            return {"bets": 0, "recovery": float("nan"), "hits": 0, "hit_rate": float("nan")}
        n = len(bets)
        hits = bets[label_col].sum()
        total_return = (bets[label_col] * bets[odds_col].fillna(0)).sum()
        return {
            "bets": n,
            "recovery": total_return / n * 100,
            "hits": int(hits),
            "hit_rate": hits / n * 100,
        }

    return {
        "win": _recovery(win_bets, "win_label", "win_odds"),
        "place": _recovery(place_bets, "place_label", "win_odds"),
        "ev_threshold": ev_threshold,
        "total_races": df["race_id"].nunique(),
        "total_horses": len(df),
    }


def plot_feature_importance(model_type: str = "win", top_n: int = 30) -> None:
    """特徴量重要度を棒グラフで表示する。"""
    import matplotlib.pyplot as plt  # CI 互換のため関数内でインポート

    model = load_latest_model(model_type)
    importance = (
        pd.Series(
            model.feature_importance(importance_type="gain"),
            index=model.feature_name(),
        )
        .sort_values(ascending=True)
        .tail(top_n)
    )

    fig, ax = plt.subplots(figsize=(10, top_n * 0.35))
    importance.plot(kind="barh", ax=ax)
    ax.set_title(f"Feature Importance ({model_type} model, gain)")
    ax.set_xlabel("Importance (gain)")
    plt.tight_layout()

    output_path = f"docs/feature_importance_{model_type}.png"
    plt.savefig(output_path, dpi=150)
    logger.info("Saved: %s", output_path)
    plt.show()


def main() -> None:
    parser = argparse.ArgumentParser(description="モデル評価")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--year", type=int, help="回収率シミュレーション対象年")
    group.add_argument("--feature-importance", action="store_true", help="特徴量重要度プロット")
    parser.add_argument("--ev-threshold", type=float, default=1.0, help="EV閾値（デフォルト: 1.0）")
    parser.add_argument(
        "--model-type", choices=["win", "place"], default="win", help="特徴量重要度対象モデル"
    )
    args = parser.parse_args()

    if args.year:
        from features.feature_builder import build_training_dataset  # noqa: PLC0415

        logger.info("Loading data for year %d...", args.year)
        df = build_training_dataset(date(args.year, 1, 1), date(args.year, 12, 31))
        if df.empty:
            logger.error("No data for year %d", args.year)
            return

        result = simulate_recovery(df, ev_threshold=args.ev_threshold)
        print(f"\n=== 回収率シミュレーション {args.year}年 (EV > {args.ev_threshold}) ===")
        print(f"  対象レース数: {result['total_races']}")
        print(f"  対象馬数:     {result['total_horses']}")
        print()
        for bet_type in ["win", "place"]:
            r = result[bet_type]
            print(f"  [{bet_type}]")
            print(f"    購入回数: {r['bets']}")
            print(f"    的中回数: {r['hits']} ({r['hit_rate']:.1f}%)")
            print(f"    回収率:   {r['recovery']:.1f}%")

    elif args.feature_importance:
        plot_feature_importance(model_type=args.model_type)


if __name__ == "__main__":
    main()
