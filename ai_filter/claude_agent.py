"""Claude API を使った SHAP 解釈・特徴量提案エージェント

SHAP による特徴量重要度を解釈し、次のバージョンで追加すべき特徴量を提案する。
ノウハウは ai_filter/knowhow.md に蓄積される。
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

KNOWHOW_PATH = Path(__file__).parent / "knowhow.md"

SYSTEM_PROMPT = """あなたは競馬予測モデルの特徴量エンジニアリングを支援するAIです。

## あなたの役割
SHAP による特徴量重要度分析を解釈し、モデルの予測精度（特に単勝回収率）を改善するための
新しい特徴量を提案してください。

## 出力フォーマット（必ず JSON で出力）
```json
{
  "interpretation": "SHAP 結果から読み取れる洞察（競馬ドメイン知識を含む）",
  "feature_suggestions": [
    {
      "name": "特徴量名（snake_case）",
      "rationale": "なぜこの特徴量が有効か（競馬的根拠）",
      "computation": "どう計算するか（SQL/Python の擬似コード）"
    }
  ],
  "knowhow_update": "今回の実験から学んだノウハウ（次回の参考に）"
}
```

## 注意
- 既に実装されている特徴量は提案しない
- 1回の提案は 2〜3 特徴量に絞る（多すぎると実装負荷が高い）
- DBから取得できるデータの範囲で考える（races, entries, results, odds テーブル）
"""


def load_knowhow() -> str:
    """蓄積されたノウハウファイルを読み込む。"""
    if KNOWHOW_PATH.exists():
        return KNOWHOW_PATH.read_text(encoding="utf-8")
    return "（まだノウハウはありません）"


def append_knowhow(new_knowhow: str, iteration: int) -> None:
    """ノウハウファイルに追記する。"""
    from datetime import date  # noqa: PLC0415

    entry = f"\n## 実験 {iteration}（{date.today()}）\n{new_knowhow}\n"
    with open(KNOWHOW_PATH, "a", encoding="utf-8") as f:
        f.write(entry)


def interpret_shap_and_suggest(
    shap_text: str,
    metrics: dict[str, float],
    existing_features: list[str],
    iteration: int,
) -> dict[str, Any]:
    """SHAP サマリーを Claude に渡して解釈・特徴量提案を得る。

    Args:
        shap_text: format_shap_for_claude() の出力
        metrics: {"mean_win_auc": ..., "mean_win_recovery": ...}
        existing_features: 現在の特徴量リスト
        iteration: 現在のイテレーション番号

    Returns:
        Claude の提案 dict（interpretation, feature_suggestions, knowhow_update）
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    knowhow = load_knowhow()

    user_message = f"""{shap_text}

## 現在のモデル性能
- win_AUC: {metrics.get("mean_win_auc", "?"):.4f}
- win_recovery: {metrics.get("mean_win_recovery", "?"):.1f}%
- place_recovery: {metrics.get("mean_place_recovery", "?"):.1f}%

## 現在の特徴量リスト
{", ".join(existing_features)}

## 蓄積されたノウハウ
{knowhow}

## お願い
上記の SHAP 分析・モデル性能・過去のノウハウを踏まえて、
win_recovery を 100% 以上にするために追加すべき特徴量を提案してください。
"""

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    content = response.content[0].text
    # JSON を抽出
    if "```json" in content:
        json_str = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        json_str = content.split("```")[1].split("```")[0].strip()
    else:
        json_str = content.strip()

    proposal = json.loads(json_str)
    logger.info("Claude proposal:\n%s", json.dumps(proposal, ensure_ascii=False, indent=2))

    # ノウハウを保存
    if proposal.get("knowhow_update"):
        append_knowhow(proposal["knowhow_update"], iteration)

    return proposal


def fetch_experiment_history() -> list[dict[str, Any]]:
    """MLflow から phase4 実験の履歴を取得する（後方互換のため残す）。"""
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
            win_auc = run.data.metrics.get("mean_win_auc")
            win_rec = run.data.metrics.get("mean_win_recovery")
            if win_auc is None or win_rec is None:
                continue
            history.append(
                {
                    "run_id": run.info.run_id,
                    "run_name": run.info.run_name,
                    "params": run.data.params,
                    "metrics": run.data.metrics,
                }
            )

    return history
