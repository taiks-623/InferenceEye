"""SHAP による特徴量重要度解析

学習済みモデルの SHAP 値を計算し、特徴量ごとの重要度サマリーを返す。
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_shap_summary(model, X: pd.DataFrame) -> list[dict]:
    """SHAP 値を計算して特徴量ごとのサマリーを返す。

    Args:
        model: 学習済み LightGBM モデル
        X: 特徴量 DataFrame

    Returns:
        重要度順にソートされた特徴量サマリーのリスト:
        [
            {
                "feature": str,
                "mean_abs_shap": float,   # 平均絶対 SHAP 値（重要度）
                "mean_shap": float,        # 平均 SHAP 値（正=勝率を上げる方向）
                "rank": int,
            },
            ...
        ]
    """
    import shap  # noqa: PLC0415

    # TreeExplainer で SHAP 値を計算（サンプル数が多い場合はサブサンプリング）
    sample_size = min(5000, len(X))
    X_sample = X.sample(n=sample_size, random_state=42) if len(X) > sample_size else X

    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_sample)

    # SHAP 値の集計
    feat_names = X.columns.tolist()
    summary = []
    for i, feat in enumerate(feat_names):
        vals = shap_values[:, i]
        summary.append(
            {
                "feature": feat,
                "mean_abs_shap": float(np.mean(np.abs(vals))),
                "mean_shap": float(np.mean(vals)),
            }
        )

    # 重要度順にソート
    summary.sort(key=lambda x: x["mean_abs_shap"], reverse=True)
    for rank, item in enumerate(summary, start=1):
        item["rank"] = rank

    return summary


def format_shap_for_claude(shap_summary: list[dict], top_n: int = 15) -> str:
    """SHAP サマリーを Claude に渡すテキスト形式に変換する。"""
    lines = ["## SHAP 特徴量重要度（上位）\n"]
    lines.append(f"{'順位':>4}  {'特徴量':<35}  {'重要度':>8}  {'方向':>6}")
    lines.append("-" * 60)

    for item in shap_summary[:top_n]:
        direction = "↑正" if item["mean_shap"] > 0 else "↓負"
        lines.append(
            f"{item['rank']:>4}  {item['feature']:<35}  "
            f"{item['mean_abs_shap']:>8.4f}  {direction:>6}"
        )

    # 重要度が低い特徴量も要約
    low_importance = [s["feature"] for s in shap_summary if s["mean_abs_shap"] < 0.001]
    if low_importance:
        lines.append(f"\n重要度が低い特徴量（mean_abs_shap < 0.001）: {', '.join(low_importance)}")

    return "\n".join(lines)
