# Phase 4 設計: AIエージェント実験ループ

## 目的

Phase 3 で AUC=0.80 を達成したが回収率は単勝 65%・複勝 80% にとどまる。
Claude API を使って実験を自動提案・評価するループを構築し、回収率 100% 超を目指す。

---

## アーキテクチャ概要

```
┌─────────────────────────────────────────────────────┐
│                 AI Experiment Loop                   │
│                                                      │
│  ┌──────────┐    提案     ┌──────────────────────┐  │
│  │          │ ──────────> │  Claude API          │  │
│  │  MLflow  │             │  (claude-opus-4-6)   │  │
│  │  結果DB  │ <────────── │  - 実験結果を分析    │  │
│  │          │    評価     │  - 次の実験を提案    │  │
│  └──────────┘             └──────────────────────┘  │
│       ↑                            │                 │
│       │ 記録                       │ 実験設定        │
│       │                            ↓                 │
│  ┌──────────────────────────────────────────────┐   │
│  │  train.py --walk-forward (実験実行)           │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

---

## 実装コンポーネント

### 1. `ai_filter/experiment_loop.py`（メインループ）

```python
def run_experiment_loop(max_iterations: int = 20) -> None:
    """AIエージェントによる実験ループを実行する。"""
    for i in range(max_iterations):
        # 過去の実験結果を MLflow から取得
        history = fetch_experiment_history()

        # Claude に次の実験を提案させる
        proposal = suggest_next_experiment(history)

        if proposal["action"] == "stop":
            break  # 収束と判断

        # 実験を実行
        result = run_experiment(proposal["config"])

        # MLflow に記録
        log_experiment(proposal, result)
```

### 2. `ai_filter/claude_agent.py`（Claude API 連携）

Claude API に渡す情報:
- 過去の実験一覧（パラメータ + win_AUC + win_recovery）
- 現状の最良結果
- 探索範囲の制約

Claude への指示（system prompt）:
```
あなたは競馬予測モデルの実験を設計するAIです。
目標: 単勝回収率を最大化する（100% 超が目標）。
制約: AUC が 0.75 を下回る実験は採用しない。
...
```

Claude の出力フォーマット（JSON）:
```json
{
  "action": "continue" | "stop",
  "rationale": "なぜこの設定を試すか",
  "config": {
    "win_params": {"num_leaves": 31, "scale_pos_weight": 20, ...},
    "ev_threshold": 1.2,
    "feature_subset": ["popularity_rank", "burden_weight_diff", ...]
  }
}
```

### 3. `model/train.py` の拡張

実験設定を外から注入できるよう引数を追加:

```bash
# 通常実行（デフォルト設定）
python model/train.py --walk-forward

# AI 提案の設定で実行
python model/train.py --walk-forward \
  --win-params '{"num_leaves": 31, "scale_pos_weight": 20}' \
  --ev-threshold 1.2
```

---

## 探索対象パラメータ

### A. LightGBM ハイパーパラメータ

| パラメータ | 現在値 | 探索範囲 |
|------------|--------|----------|
| `num_leaves` | 63 | 15〜127 |
| `scale_pos_weight`（単勝） | 13 | 5〜30 |
| `learning_rate` | 0.05 | 0.01〜0.1 |
| `min_child_samples` | 50 | 20〜100 |
| `feature_fraction` | 0.8 | 0.5〜1.0 |

### B. 買い目戦略

| パラメータ | 現在値 | 探索範囲 |
|------------|--------|----------|
| `ev_threshold` | 1.0 | 0.8〜2.0 |
| `val_start`/`val_end` | 2020〜2024 | 変更なし |

### C. 特徴量サブセット

- `popularity_rank` を外したモデル（市場情報なし）
- 騎手・調教師特徴量のみで予測
- 馬の過去成績のみで予測

---

## 評価指標と収束条件

### 主指標
- `win_recovery`（EV > ev_threshold で購入した場合の回収率）
- 目標: 100% 以上

### 副指標
- `win_AUC`（0.75 未満になった場合はその実験設定を棄却）

### 収束条件
- 連続 5 回改善なし → 停止
- `win_recovery > 100%` かつ `win_AUC > 0.75` → 成功として停止
- `max_iterations` に達した → 強制停止

---

## 実装順序

1. `train.py` に外部パラメータ注入（`--win-params` 引数）を追加
2. `ai_filter/claude_agent.py` 実装（MLflow 読み取り + Claude API 呼び出し）
3. `ai_filter/experiment_loop.py` 実装（ループ制御）
4. 動作確認（10〜20 イテレーション実行）

## 注意点

- Claude API への入力は実験履歴のサマリーのみ（全データを渡さない）
- 1 イテレーション = 1回の walk-forward（5年分）≈ 数分
- MLflow の experiment 名: `phase4_ai_loop`
- API コスト管理: max_iterations で上限を設ける
