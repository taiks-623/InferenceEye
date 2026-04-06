# InferenceEye — Claude Code 指示書

## プロジェクト概要

JRA（中央競馬）の過去レースデータを機械学習で分析し、期待値の高い買い目を自動でDiscordに通知するシステム。

- **予測対象**: 単勝・複勝
- **データソース**: netkeiba（スクレイピング）
- **通知手段**: Discord Bot
- **詳細設計**: `InferenceEye_design.md`（ローカルのみ・git管理外）を参照すること

## 技術スタック

| カテゴリ | 技術 |
|---------|------|
| 言語 | Python |
| コンテナ | Docker / Docker Compose |
| DB | PostgreSQL |
| スクレイピング | requests + BeautifulSoup4 / Playwright |
| ML | LightGBM |
| 実験管理 | MLflow |
| スケジューラ | APScheduler |
| AIフィルタ | Claude API (claude-opus-4-6) |
| 通知 | Discord Bot |

## 開発ルール

### 実装前に設計ドキュメントを書く

各フェーズ・各機能の実装前に必ず `docs/design/` に設計ドキュメントを作成する。
設計レビューが済んでから実装に入ること。

### ドキュメント管理

- `docs/design/` — 各フェーズの詳細設計
- `docs/decisions/` — 技術選定・設計意図（ADR形式: `NNN-title.md`）
- `docs/learnings/` — 作業中に学んだこと・ハマりポイント

### コーディング規約

- フォーマッタ: `ruff format`
- リンター: `ruff check`
- テスト: `pytest`
- 型アノテーションを積極的に使う

### ブランチ戦略（重要）

**main ブランチでは直接作業しない。** 必ずブランチを切ってから作業すること。

#### 作業の流れ

```
1. Issue を確認する
2. ブランチを切る:  git checkout -b feature/issue-番号-短い説明
3. 実装・コミットを行う
4. PR を作成する:  gh pr create
5. CI（ruff + pytest）が通ることを確認する
6. main にマージする
7. Issue が自動クローズされていない場合は手動でクローズする
```

#### ブランチ命名規則

```
feature/issue-1-docker-compose    # 新機能・実装
fix/issue-XX-説明                 # バグ修正
docs/issue-XX-説明                # ドキュメントのみの変更
```

#### PR マージの条件

- CI（GitHub Actions）がすべてパスしていること
- `main` への直接 push は行わない

### GitHub Issue 管理

- 各作業は対応する GitHub Issue に紐づけて進める
- Issue の作業が完了したら必ず `gh issue close <番号>` でクローズする
- Milestone（Phase 1〜5）で進捗を管理する
- PR のコミットメッセージまたは本文に `closes #番号` を含めると main マージ時に自動でクローズされる

### コミットメッセージ

```
<type>: <概要>

<詳細（任意）>
```

type: `feat` / `fix` / `docs` / `refactor` / `test` / `chore`

## ディレクトリ構成

```
inference-eye/
├── CLAUDE.md
├── .gitignore
├── docs/
│   ├── design/       # 各フェーズの詳細設計
│   ├── decisions/    # ADR（技術選定記録）
│   └── learnings/    # 学習メモ
├── db/
│   └── init.sql
├── scraper/
├── features/
├── model/
├── pipeline/
├── ai_filter/
└── notify/
```

## フェーズ計画

| フェーズ | 内容 |
|---------|------|
| Phase 1 | 環境構築（Docker + PostgreSQL + MLflow） |
| Phase 2 | スクレイピング実装 |
| Phase 3 | 特徴量・モデル実装 |
| Phase 4 | AIエージェント実験ループ |
| Phase 5 | 通知・自動化 |
| Phase 6 | Webアプリ化（将来） |
