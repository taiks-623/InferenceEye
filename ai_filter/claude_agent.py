"""Claude API を使った実験提案エージェント

過去の実験結果（MLflow）を読み取り、次に試すべき実験設定を提案する。
"""

import json
import logging
import os
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """あなたは競馬予測モデルの実験を設計するAIエージェントです。

## 目標
- 単勝回収率（win_recovery）を最大化する。100% 以上が達成目標。
- AUC が 0.75 を下回る実験設定は採用しない。

## あなたの役割
過去の実験結果を分析し、次に試すべき実験設定を JSON で提案してください。

## 探索対象パラメータ
- LightGBM パラメータ: num_leaves (15〜127), scale_pos_weight (5〜30), learning_rate (0.01〜0.1), min_child_samples (20〜100), feature_fraction (0.5〜1.0)
- ev_threshold: EV閾値 (0.8〜2.0) — 高くするほど買い目を絞る
- feature_subset: 使用する特徴量リスト（null の場合は全特徴量）

## 出力フォーマット（必ず JSON で出力）
```json
{
  "action": "continue" または "stop",
  "rationale": "この実験設定を試す理由・仮説",
  "config": {
    "win_params": {"num_leaves": 63, "scale_pos_weight": 13, ...},
    "ev_threshold": 1.0,
    "feature_subset": null
  }
}
```

action が "stop" の場合は収束と判断してループを終了します。
"""


def fetch_experiment_history() -> list[dict[str, Any]]:
    """MLflow から過去の実験結果を取得する。"""
    import mlflow  # noqa: PLC0415

    client = mlflow.tracking.MlflowClient()
    experiments = client.search_experiments(filter_string="name LIKE 'phase4%'")

    history = []
    for exp in experiments:
        runs = client.search_runs(
            experiment_ids=[exp.experiment_id],
            order_by=["start_time DESC"],
            max_results=50,
        )
        for run in runs:
            if run.data.metrics:
                history.append(
                    {
                        "run_id": run.info.run_id,
                        "run_name": run.info.run_name,
                        "params": run.data.params,
                        "metrics": run.data.metrics,
                    }
                )

    return history


def suggest_next_experiment(
    history: list[dict[str, Any]],
    iteration: int,
) -> dict[str, Any]:
    """Claude API を使って次の実験設定を提案する。"""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # 過去の実験をサマリー形式に変換
    if history:
        history_text = "## 過去の実験結果\n\n"
        for h in history[-20:]:  # 直近20件
            metrics = h.get("metrics", {})
            params = h.get("params", {})
            # mean_win_auc / mean_win_recovery がない run（nested step など）はスキップ
            win_auc = metrics.get("mean_win_auc")
            win_rec = metrics.get("mean_win_recovery")
            if win_auc is None or win_rec is None:
                continue
            history_text += (
                f"- run: {h['run_name']}\n"
                f"  params: num_leaves={params.get('num_leaves', '?')}, "
                f"scale_pos_weight={params.get('scale_pos_weight', '?')}, "
                f"ev_threshold={params.get('ev_threshold', '?')}\n"
                f"  metrics: win_AUC={win_auc:.4f}, win_recovery={win_rec:.1f}%\n"
            )
        if not history_text.strip().endswith("\n\n"):
            history_text = history_text or "## 過去の実験結果\n\nまだ集計データがありません。\n"
    else:
        history_text = (
            "## 過去の実験結果\n\nまだ実験データがありません。最初の実験を提案してください。\n"
        )

    user_message = f"""{history_text}

## 現在のイテレーション
{iteration + 1} 回目の実験提案をお願いします。

過去の結果を踏まえて、win_recovery を改善できると考える次の実験設定を JSON で提案してください。
連続 5 回改善なしまたは win_recovery > 100% 達成の場合は action を "stop" にしてください。
"""

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    # JSON を抽出
    content = response.content[0].text
    # コードブロック内の JSON を取得
    if "```json" in content:
        json_str = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        json_str = content.split("```")[1].split("```")[0].strip()
    else:
        json_str = content.strip()

    proposal = json.loads(json_str)
    logger.info("Claude proposal: %s", json.dumps(proposal, ensure_ascii=False, indent=2))
    return proposal
