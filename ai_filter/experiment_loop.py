"""Phase 4 AIエージェント実験ループ

Claude API が実験設定を提案 → train.py で実行 → MLflow に記録 → 繰り返す。

実行例:
    python ai_filter/experiment_loop.py
    python ai_filter/experiment_loop.py --max-iterations 30
"""

import argparse
import json
import logging
from datetime import date

import mlflow
from ai_filter.claude_agent import fetch_experiment_history, suggest_next_experiment

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

EXPERIMENT_NAME = "phase4_ai_loop"


def run_experiment(config: dict, df) -> dict:
    """1回の実験を実行して結果を返す。"""
    from model.train import walk_forward_validation  # noqa: PLC0415

    win_params = config.get("win_params")
    ev_threshold = config.get("ev_threshold", 1.0)
    feature_subset = config.get("feature_subset")

    summary = walk_forward_validation(
        df,
        win_params=win_params,
        ev_threshold=ev_threshold,
        feature_subset=feature_subset,
    )
    return summary


def run_loop(max_iterations: int = 20) -> None:
    """AIエージェント実験ループを実行する。"""

    from features.feature_builder import build_training_dataset  # noqa: PLC0415
    from model.train import DATA_START  # noqa: PLC0415

    logger.info("Loading training data...")
    df = build_training_dataset(DATA_START, date.today())

    if df.empty:
        logger.error("No training data. Abort.")
        return

    mlflow.set_experiment(EXPERIMENT_NAME)

    best_recovery = float("-inf")
    no_improve_count = 0

    for iteration in range(max_iterations):
        logger.info("=== Iteration %d / %d ===", iteration + 1, max_iterations)

        # 過去の実験履歴を取得
        history = fetch_experiment_history()

        # Claude に次の実験を提案させる
        proposal = suggest_next_experiment(history, iteration)

        if proposal.get("action") == "stop":
            logger.info("Claude decided to stop: %s", proposal.get("rationale"))
            break

        config = proposal.get("config", {})
        rationale = proposal.get("rationale", "")
        logger.info("Rationale: %s", rationale)

        # 実験実行
        with mlflow.start_run(run_name=f"phase4_iter_{iteration + 1:02d}"):
            mlflow.log_param("iteration", iteration + 1)
            mlflow.log_param("rationale", rationale)
            mlflow.log_param("ev_threshold", config.get("ev_threshold", 1.0))
            if config.get("win_params"):
                for k, v in config["win_params"].items():
                    mlflow.log_param(k, v)
            if config.get("feature_subset"):
                mlflow.log_param("feature_subset", json.dumps(config["feature_subset"]))

            summary = run_experiment(config, df)

            mlflow.log_metrics(
                {
                    "mean_win_auc": summary["mean_win_auc"],
                    "mean_place_auc": summary["mean_place_auc"],
                    "mean_win_recovery": summary["mean_win_recovery"],
                    "mean_place_recovery": summary["mean_place_recovery"],
                }
            )

        win_recovery = summary["mean_win_recovery"]
        logger.info(
            "Result: win_AUC=%.4f win_recovery=%.1f%%",
            summary["mean_win_auc"],
            win_recovery,
        )

        # 改善チェック
        if win_recovery > best_recovery:
            best_recovery = win_recovery
            no_improve_count = 0
            logger.info("New best win_recovery: %.1f%%", best_recovery)
        else:
            no_improve_count += 1
            logger.info("No improvement (%d / 5)", no_improve_count)

        if no_improve_count >= 5:
            logger.info("No improvement for 5 iterations. Stopping.")
            break

        if win_recovery >= 100.0 and summary["mean_win_auc"] >= 0.75:
            logger.info("Target achieved! win_recovery=%.1f%%", win_recovery)
            break

    logger.info("Loop finished. Best win_recovery: %.1f%%", best_recovery)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 4 AIエージェント実験ループ")
    parser.add_argument("--max-iterations", type=int, default=20, help="最大イテレーション数")
    args = parser.parse_args()
    run_loop(args.max_iterations)


if __name__ == "__main__":
    main()
