# Phase 4 設計: Optuna パラメータ最適化 + Claude 特徴量解釈ループ（v2）

## 背景と設計変更

初版（v1）では Claude API にパラメータチューニングをさせていたが、
数値最適化は Optuna の方が効率的・安価。Claude の強みは「特徴量の意味解釈と提案」にある。

---

## 新設計: 役割分担

| コンポーネント | 役割 |
|----------------|------|
| **Optuna** | `num_leaves`, `scale_pos_weight`, `ev_threshold` 等の数値最適化 |
| **SHAP** | 特徴量重要度の定量計算 |
| **Claude API** | SHAP 結果の解釈・競馬ドメイン知識を踏まえた新特徴量の提案 |

---

## フロー

```
┌─────────────────────────────────────────────────────────┐
│ フェーズ A: パラメータ最適化（Optuna）                    │
│                                                          │
│  Optuna trial → walk-forward → win_recovery を目標値    │
│  → 最良パラメータを決定                                  │
└──────────────────────────┬──────────────────────────────┘
                            │ 最良パラメータ
┌──────────────────────────▼──────────────────────────────┐
│ フェーズ B: SHAP 解析                                     │
│                                                          │
│  最良パラメータでモデルを再学習                          │
│  → SHAP values を計算                                    │
│  → 特徴量ごとの重要度・方向性をサマリー化               │
└──────────────────────────┬──────────────────────────────┘
                            │ SHAP サマリー
┌──────────────────────────▼──────────────────────────────┐
│ フェーズ C: Claude による解釈・提案                       │
│                                                          │
│  SHAP サマリー + 過去ノウハウを Claude に渡す           │
│  → 「なぜ効いているか」の解釈を生成                     │
│  → 新特徴量の提案（コード仕様まで含む）                 │
│  → ノウハウファイルに追記                               │
└──────────────────────────┬──────────────────────────────┘
                            │ 実装（人間 or 自動生成）
┌──────────────────────────▼──────────────────────────────┐
│ feature_builder_v{N}.py（特徴量バージョン管理）           │
│  → フェーズ A に戻る                                     │
└─────────────────────────────────────────────────────────┘
```

---

## 実装コンポーネント

### 1. `ai_filter/optuna_tuner.py`

```python
def optimize(df: pd.DataFrame, n_trials: int = 50) -> dict:
    """Optuna でハイパーパラメータを最適化し最良設定を返す。"""
    def objective(trial):
        win_params = {
            "num_leaves": trial.suggest_int("num_leaves", 15, 127),
            "scale_pos_weight": trial.suggest_float("scale_pos_weight", 5.0, 30.0),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.1, log=True),
            "min_child_samples": trial.suggest_int("min_child_samples", 20, 100),
        }
        ev_threshold = trial.suggest_float("ev_threshold", 0.8, 2.0)
        summary = walk_forward_validation(df, win_params=win_params, ev_threshold=ev_threshold)
        return summary["mean_win_recovery"]

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials)
    return study.best_params
```

### 2. `ai_filter/shap_analyzer.py`

```python
def compute_shap_summary(model, X: pd.DataFrame, feat_cols: list[str]) -> dict:
    """SHAP 値を計算して特徴量ごとのサマリーを返す。
    
    Returns:
        {
            "feature_name": {
                "mean_abs_shap": float,   # 平均絶対 SHAP 値（重要度）
                "mean_shap": float,        # 平均 SHAP 値（正=正の影響）
                "rank": int,               # 重要度順位
            }
        }
    """
```

### 3. `ai_filter/claude_agent.py`（改修）

Claude への入力:
- SHAP サマリー（特徴量重要度ランキング + 方向性）
- 過去の特徴量ノウハウ（`ai_filter/knowhow.md`）
- 現在の win_recovery・AUC

Claude からの出力:
```json
{
  "interpretation": "popularity_rank の SHAP が最大だが...",
  "feature_suggestions": [
    {
      "name": "days_to_next_grade_up",
      "rationale": "昇級戦は過去成績との乖離が大きい...",
      "sql_hint": "SELECT MAX(CASE WHEN class_rank > prev_class_rank THEN 1 ELSE 0 END)..."
    }
  ],
  "knowhow": "【2026-04-10追記】popularity_rank はほぼ全馬に効く汎用特徴量..."
}
```

### 4. `ai_filter/knowhow.md`

Claude が蓄積するノウハウファイル。
実験を重ねるごとに追記され、次の解釈の文脈として使用される。

---

## 特徴量バージョン管理

```
features/
  feature_builder.py      ← 現行（v1）
  feature_builder_v2.py   ← Claude 提案の特徴量を追加
  feature_builder_v3.py   ← さらに追加
```

`train.py` / `experiment_loop.py` は `--feature-version v2` 等で切り替え可能にする。

---

## 実装優先順位

1. `ai_filter/optuna_tuner.py` — Optuna パラメータ最適化
2. `ai_filter/shap_analyzer.py` — SHAP 計算
3. `ai_filter/claude_agent.py` 改修 — SHAP 解釈・提案
4. `ai_filter/knowhow.md` + `experiment_loop.py` 改修 — 統合ループ
