"""Optuna によるハイパーパラメータ最適化

walk-forward 検証の win_recovery を最大化するパラメータを探索する。

実行例:
    python ai_filter/optuna_tuner.py --n-trials 50
    python ai_filter/optuna_tuner.py --n-trials 30 --output best_params.json
"""

import argparse
import json
import logging
from datetime import date

import optuna

optuna.logging.set_verbosity(optuna.logging.WARNING)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def build_objective(df):
    """Optuna の objective 関数を返す。"""
    from model.train import walk_forward_validation  # noqa: PLC0415

    def objective(trial: optuna.Trial) -> float:
        win_params = {
            "num_leaves": trial.suggest_int("num_leaves", 15, 127),
            "scale_pos_weight": trial.suggest_float("scale_pos_weight", 5.0, 30.0),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.1, log=True),
            "min_child_samples": trial.suggest_int("min_child_samples", 20, 100),
            "feature_fraction": trial.suggest_float("feature_fraction", 0.5, 1.0),
        }
        ev_threshold = trial.suggest_float("ev_threshold", 0.8, 2.0)

        summary = walk_forward_validation(
            df,
            win_params=win_params,
            ev_threshold=ev_threshold,
        )

        win_recovery = summary["mean_win_recovery"]
        win_auc = summary["mean_win_auc"]

        # AUC が 0.75 未満の場合はペナルティ
        if win_auc < 0.75:
            return float("-inf")

        logger.info(
            "Trial %d: win_recovery=%.1f%% win_AUC=%.4f | params=%s ev_threshold=%.2f",
            trial.number,
            win_recovery,
            win_auc,
            {k: round(v, 4) if isinstance(v, float) else v for k, v in win_params.items()},
            ev_threshold,
        )
        return win_recovery

    return objective


def optimize(df, n_trials: int = 50) -> dict:
    """Optuna でパラメータを最適化し、最良パラメータを返す。"""
    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42),
    )
    study.optimize(build_objective(df), n_trials=n_trials, show_progress_bar=False)

    best = study.best_params
    logger.info("Best win_recovery: %.1f%%", study.best_value)
    logger.info("Best params: %s", best)
    return best


def main() -> None:
    parser = argparse.ArgumentParser(description="Optuna ハイパーパラメータ最適化")
    parser.add_argument("--n-trials", type=int, default=50, help="試行回数")
    parser.add_argument("--output", type=str, default=None, help="結果を JSON で保存するパス")
    args = parser.parse_args()

    from features.feature_builder import build_training_dataset  # noqa: PLC0415
    from model.train import DATA_START  # noqa: PLC0415

    logger.info("Loading training data...")
    df = build_training_dataset(DATA_START, date.today())

    if df.empty:
        logger.error("No training data. Abort.")
        return

    best_params = optimize(df, args.n_trials)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(best_params, f, indent=2)
        logger.info("Saved best params to %s", args.output)
    else:
        print("\n=== 最良パラメータ ===")
        print(json.dumps(best_params, indent=2))


if __name__ == "__main__":
    main()
