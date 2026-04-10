"""Phase 4 実験ループ: Optuna パラメータ最適化 + SHAP 解析 + Claude 特徴量提案

フロー:
  フェーズ A: Optuna でハイパーパラメータを最適化
  フェーズ B: 最良パラメータで SHAP 解析
  フェーズ C: Claude が SHAP を解釈し、新特徴量を提案

実行例:
    python ai_filter/experiment_loop.py
    python ai_filter/experiment_loop.py --optuna-trials 30 --max-iterations 5
"""

import argparse
import logging
from datetime import date

from ai_filter.claude_agent import interpret_shap_and_suggest
from ai_filter.optuna_tuner import optimize
from ai_filter.shap_analyzer import compute_shap_summary, format_shap_for_claude

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run_shap_analysis(df, best_params: dict) -> tuple[list[dict], dict]:
    """最良パラメータでモデルを学習し、SHAP サマリーと検証メトリクスを返す。

    Args:
        df: 学習データ DataFrame
        best_params: Optuna で得た最良パラメータ（win_params + ev_threshold）

    Returns:
        (shap_summary, metrics)
        - shap_summary: compute_shap_summary の出力（feature, mean_abs_shap, mean_shap, rank）
        - metrics: mean_win_auc, mean_win_recovery, mean_place_recovery
    """
    from model.train import FEATURE_COLS, WIN_PARAMS, walk_forward_validation  # noqa: PLC0415

    ev_threshold = best_params.pop("ev_threshold", 1.0)
    win_params = {**WIN_PARAMS, **best_params}

    # walk-forward で検証メトリクスを取得（最後の fold のモデルを SHAP に使う）
    summary = walk_forward_validation(df, win_params=win_params, ev_threshold=ev_threshold)
    metrics = {
        "mean_win_auc": summary["mean_win_auc"],
        "mean_win_recovery": summary["mean_win_recovery"],
        "mean_place_recovery": summary["mean_place_recovery"],
    }

    # 最後の fold のモデルで SHAP を計算
    import lightgbm as lgb  # noqa: PLC0415

    from model.train import _coerce_feature_dtypes  # noqa: PLC0415

    feat_cols = [c for c in FEATURE_COLS if c in df.columns]
    df_coerced = _coerce_feature_dtypes(df, feat_cols)
    X = df_coerced[feat_cols].copy()

    # 全データで再学習（SHAP は全体傾向を把握するため）
    y = df_coerced["win_label"]
    lgb_params = {**win_params, "n_estimators": 300, "random_state": 42}
    lgb_params.setdefault("verbosity", -1)
    model = lgb.LGBMClassifier(**lgb_params)
    model.fit(X, y)

    shap_summary = compute_shap_summary(model, X)
    return shap_summary, metrics


def run_loop(max_iterations: int = 3, optuna_trials: int = 50) -> None:
    """Optuna → SHAP → Claude の実験ループを実行する。"""
    from features.feature_builder import build_training_dataset  # noqa: PLC0415
    from model.train import DATA_START, FEATURE_COLS  # noqa: PLC0415

    logger.info("Loading training data...")
    df = build_training_dataset(DATA_START, date.today())

    if df.empty:
        logger.error("No training data. Abort.")
        return

    existing_features = [c for c in FEATURE_COLS if c in df.columns]

    for iteration in range(max_iterations):
        logger.info("=== Iteration %d / %d ===", iteration + 1, max_iterations)

        # フェーズ A: Optuna パラメータ最適化
        logger.info("[Phase A] Optuna optimization (%d trials)...", optuna_trials)
        best_params = optimize(df, n_trials=optuna_trials)
        logger.info("Best params: %s", best_params)

        # フェーズ B: SHAP 解析
        logger.info("[Phase B] SHAP analysis with best params...")
        shap_summary, metrics = run_shap_analysis(df, best_params.copy())
        shap_text = format_shap_for_claude(shap_summary, top_n=15)
        logger.info(
            "Metrics: win_AUC=%.4f win_recovery=%.1f%% place_recovery=%.1f%%",
            metrics["mean_win_auc"],
            metrics["mean_win_recovery"],
            metrics["mean_place_recovery"],
        )

        # フェーズ C: Claude による解釈・特徴量提案
        logger.info("[Phase C] Claude interpretation and feature suggestions...")
        proposal = interpret_shap_and_suggest(
            shap_text=shap_text,
            metrics=metrics,
            existing_features=existing_features,
            iteration=iteration + 1,
        )

        logger.info("=== Claude Proposal (Iteration %d) ===", iteration + 1)
        logger.info("Interpretation: %s", proposal.get("interpretation", ""))
        for i, feat in enumerate(proposal.get("feature_suggestions", []), start=1):
            logger.info(
                "  [%d] %s — %s",
                i,
                feat.get("name", "?"),
                feat.get("rationale", ""),
            )
            logger.info("       Computation: %s", feat.get("computation", ""))

        if metrics["mean_win_recovery"] >= 100.0 and metrics["mean_win_auc"] >= 0.75:
            logger.info("Target achieved! win_recovery=%.1f%%", metrics["mean_win_recovery"])
            break

    logger.info("Loop finished.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 4 Optuna+SHAP+Claude 実験ループ")
    parser.add_argument("--max-iterations", type=int, default=3, help="最大イテレーション数")
    parser.add_argument("--optuna-trials", type=int, default=50, help="Optuna 試行回数")
    args = parser.parse_args()
    run_loop(max_iterations=args.max_iterations, optuna_trials=args.optuna_trials)


if __name__ == "__main__":
    main()
