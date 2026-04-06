# Phase 1: 環境構築 詳細設計

関連 Issue: #1 #2 #3 #4

---

## 概要

このフェーズでは、InferenceEye のすべての処理が動く基盤を構築する。
Docker Compose で複数のサービス（PostgreSQL・MLflow・アプリ）を一括管理し、
`docker compose up` 一発で開発環境が再現できる状態を目指す。

### 完了の定義

- `docker compose up -d` で全サービスが起動する
- PostgreSQL に全テーブルが作成されている
- MLflow UI（`http://localhost:5001`）にブラウザでアクセスできる
- Python から DB 接続・MLflow へのログが動作する
- GitHub Actions CI（ruff + pytest）がパスする

---

## 1. 全体アーキテクチャ

### サービス構成

```
docker compose up
  ├── postgres     PostgreSQL 16（DB本体）
  ├── mlflow       MLflow Tracking Server（実験管理UI）
  └── app          Python アプリケーション（スクレイパー・モデル等）
```

### ネットワーク・ポート

| サービス | コンテナ内ポート | ホスト公開ポート | 用途 |
|---------|--------------|--------------|------|
| postgres | 5432 | 5432 | DB接続 |
| mlflow | 5000 | 5001 | MLflow UI（macOS AirPlay が 5000 を使用するため） |
| app | — | — | 常駐しない（スクリプト実行用） |

### データ永続化

PostgreSQL のデータは Docker Volume で永続化する。
コンテナを削除してもデータは残る。

```
volumes:
  postgres_data:   ← DB ファイルを永続化
  mlflow_data:     ← MLflow の実験ログを永続化
```

---

## 2. ディレクトリ構成（Phase 1 終了時点）

```
inference-eye/
├── docker-compose.yml
├── .env                    # 実際の値（gitignore対象）
├── .env.example            # 変数名だけ記載したテンプレート（git管理）
├── db/
│   └── init.sql            # テーブル定義・初期データ
├── app/
│   ├── Dockerfile
│   └── requirements.txt
└── mlflow/
    └── Dockerfile          # MLflow のカスタム設定が必要な場合のみ
```

---

## 3. docker-compose.yml 設計

### 全体構成

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./db/init.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
      timeout: 5s
      retries: 5

  mlflow:
    build: ./mlflow  # psycopg2 を追加した独自イメージ（公式イメージには含まれない）
    ports:
      - "5001:5000"  # macOS AirPlay が 5000 を使用するため 5001 にマッピング
    environment:
      MLFLOW_BACKEND_STORE_URI: postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      MLFLOW_DEFAULT_ARTIFACT_ROOT: /mlflow/artifacts
    volumes:
      - mlflow_data:/mlflow/artifacts
    depends_on:
      postgres:
        condition: service_healthy
    command: >
      mlflow server
      --backend-store-uri postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      --default-artifact-root /mlflow/artifacts
      --host 0.0.0.0
      --port 5000

  app:
    build: ./app
    environment:
      DATABASE_URL: postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      MLFLOW_TRACKING_URI: http://mlflow:5000
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      DISCORD_BOT_TOKEN: ${DISCORD_BOT_TOKEN}
    volumes:
      - .:/workspace
    working_dir: /workspace
    depends_on:
      postgres:
        condition: service_healthy
    command: tail -f /dev/null   # 常時起動（スクリプトは docker compose exec で実行）

volumes:
  postgres_data:
  mlflow_data:
```

### ポイント解説

**`healthcheck`（postgres）**
PostgreSQL の起動には数秒かかる。`healthcheck` を設定することで、
MLflow と app は「PostgreSQL が本当に準備できてから」起動する。
`depends_on: condition: service_healthy` がこれを実現する。

**`command: tail -f /dev/null`（app）**
app コンテナはすぐ終了しないよう `tail -f /dev/null` で待機させる。
スクリプトの実行は `docker compose exec app python scraper/scrape_results.py` のように行う。

**MLflow のバックエンドストアを PostgreSQL に統一**
MLflow のメタデータ（実験・パラメータ・メトリクス）も PostgreSQL で管理する。
これにより MLflow 専用の SQLite ファイルが不要になり、バックアップが一元化できる。

**MLflow に psycopg2 が必要**
公式イメージ `ghcr.io/mlflow/mlflow` には psycopg2 が含まれていない。
PostgreSQL をバックエンドストアとして使う場合は独自 Dockerfile で追加する必要がある。
→ `mlflow/Dockerfile` を参照。

**macOS でのポート競合**
macOS Monterey 以降は AirPlay 受信機能（ControlCenter）がポート 5000 を使用する。
そのためホスト側ポートを 5001 にマッピングしている。MLflow UI へのアクセスは `http://localhost:5001`。

---

## 4. 環境変数設計

### .env.example（git管理・公開OK）

```
# PostgreSQL
POSTGRES_DB=inferenceeye
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password_here

# API Keys
ANTHROPIC_API_KEY=your_anthropic_api_key_here
DISCORD_BOT_TOKEN=your_discord_bot_token_here
```

### .env（gitignore・非公開）

`.env.example` をコピーして実際の値を記入する。

```bash
cp .env.example .env
# .env を編集して実際の値を入れる
```

---

## 5. Dockerfile（app）設計

```dockerfile
FROM python:3.12-slim

# システムパッケージ（Playwright 用に必要）
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright ブラウザのインストール
RUN playwright install chromium --with-deps
```

### ベースイメージに `python:3.12-slim` を選ぶ理由

| イメージ | サイズ | 採用理由 |
|---------|-------|---------|
| `python:3.12` | 大 | 不要なものが多い |
| `python:3.12-slim` | 中 | 必要最低限・`apt-get` が使える |
| `python:3.12-alpine` | 小 | Playwright との相性が悪い |

Playwright（Chromium）は glibc に依存するため Alpine は不可。slim を選択。

---

## 6. requirements.txt 設計

```
# DB
psycopg2-binary==2.9.10

# スクレイピング
requests==2.32.3
beautifulsoup4==4.13.3
lxml==5.3.0
playwright==1.51.0

# ML
lightgbm==4.6.0
scikit-learn==1.6.1
pandas==2.2.3
numpy==2.2.4

# 実験管理
mlflow==2.19.0

# スケジューラ
APScheduler==3.11.0

# AI
anthropic==0.50.0

# Discord
discord.py==2.4.0

# 可視化・分析
matplotlib==3.10.1
seaborn==0.13.2
japanize-matplotlib==1.1.3  # matplotlib で日本語フォントを使えるようにする
shap==0.47.2                 # 特徴量 importance の可視化（SHAP 値）

# Jupyter
jupyter==1.1.1
ipykernel==6.29.5

# 開発・テスト
ruff==0.11.2
pytest==8.3.5
pytest-cov==6.1.0
```

### バージョンを固定する理由

バージョンを固定しないと `pip install` のたびに動作が変わる可能性がある。
特に `lightgbm` や `playwright` は破壊的変更が起きやすいため必ず固定する。

---

## 7. PostgreSQL スキーマ（db/init.sql）設計

### テーブル一覧と依存関係

```
venues
  └── races（venue_code → venues）
        ├── entries（race_id → races）
        │     ├── horses（horse_id → horses ※自己参照あり）
        │     ├── jockeys（jockey_id → jockeys）
        │     └── trainers（trainer_id → trainers）
        ├── results（race_id, horse_num → entries）
        ├── odds（race_id → races）
        ├── training_times（horse_id → horses）
        ├── track_bias_log（venue_code → venues）
        ├── ai_assessments（race_id → races）
        └── predictions（race_id → races）
race_calendars（独立）
```

### 外部キー制約の注意点

`horses` テーブルは `father_id` / `mother_id` が自己参照（同テーブル参照）になっている。
また `horses.trainer_id` は `trainers` を参照するが、
`trainers` より先に `horses` を INSERT しようとするとエラーになる。

**init.sql での CREATE 順序（依存関係を考慮）:**

```
1. venues
2. jockeys
3. trainers
4. horses（trainers に依存）
5. races（venues に依存）
6. entries（races / horses / jockeys / trainers に依存）
7. results（entries に依存）
8. odds（races に依存）
9. training_times（horses に依存）
10. track_bias_log（venues に依存）
11. ai_assessments（races に依存）
12. predictions（races に依存）
13. race_calendars（独立）
```

### 初期データ（venues）

venues テーブルは JRA 10 場の固定データなので init.sql に INSERT しておく。

```sql
INSERT INTO venues (venue_code, venue_name) VALUES
  ('01', '札幌'), ('02', '函館'), ('03', '福島'), ('04', '新潟'),
  ('05', '東京'), ('06', '中山'), ('07', '中京'), ('08', '京都'),
  ('09', '阪神'), ('10', '小倉')
ON CONFLICT DO NOTHING;
```

---

## 8. GitHub Actions CI 設計（.github/workflows/ci.yml）

既に作成済み。動作内容の説明：

```
push to main または PR 作成時に自動実行
  1. Python 3.12 セットアップ
  2. ruff check .       → コードの文法・品質チェック
  3. ruff format --check → フォーマット崩れの検出
  4. pytest             → 単体テスト実行
```

Phase 1 時点ではテストが少ないため `pytest || true` でエラーにしない設定にしている。
Phase 2 以降、テストが揃ってきたら `|| true` を外す。

---

## 9. 実装手順（ステップバイステップ）

### Step 1: リポジトリのクローン・.env 作成

```bash
git clone https://github.com/taiks-623/InferenceEye.git
cd InferenceEye
cp .env.example .env
# .env を編集して POSTGRES_PASSWORD を設定（他は後でOK）
```

### Step 2: ディレクトリ・ファイルの作成

```bash
mkdir -p db app mlflow
# 各ファイルを作成（後続のIssueで実装）
```

### Step 3: docker compose 起動確認

```bash
docker compose up -d
docker compose ps        # 全サービスが "running" になっていることを確認
docker compose logs -f   # ログを確認
```

### Step 4: PostgreSQL 接続確認

```bash
docker compose exec postgres psql -U postgres -d inferenceeye
# psql に入れたら成功
\dt   # テーブル一覧を表示（init.sql が正しく実行されていれば全テーブルが見える）
\q    # 終了
```

### Step 5: MLflow UI 確認

ブラウザで `http://localhost:5001` を開き、MLflow の画面が表示されればOK。

### Step 5.5: Jupyter Notebook を開く

ブラウザで `http://localhost:8888` を開く（パスワード不要）。

Notebook 内から DB・MLflow に接続する場合はコンテナ内部のアドレスを使う：

```python
import os
import psycopg2
import mlflow

# PostgreSQL 接続（DATABASE_URL 環境変数が自動で設定されている）
conn = psycopg2.connect(os.environ["DATABASE_URL"])

# MLflow（コンテナ間通信はポート 5000 を使う）
mlflow.set_tracking_uri("http://mlflow:5000")

with mlflow.start_run(run_name="notebook_test"):
    mlflow.log_param("test", "ok")
```

> **注意:** ブラウザからは `http://localhost:5001` だが、コンテナ内部からは `http://mlflow:5000`（内部ポート）を使う。

### Step 6: Python から動作確認

```bash
docker compose exec app python - <<'EOF'
import psycopg2, os, mlflow

# DB 接続確認
conn = psycopg2.connect(os.environ["DATABASE_URL"])
print("PostgreSQL: OK")
conn.close()

# MLflow 確認
mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
with mlflow.start_run(run_name="connection_test"):
    mlflow.log_param("test", "ok")
print("MLflow: OK")
EOF
```

---

## 10. トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| postgres が起動しない | .env の POSTGRES_PASSWORD が未設定 | `.env` を確認 |
| mlflow が起動しない | postgres の起動を待てていない | `depends_on: condition: service_healthy` を確認 |
| `psql: FATAL: role does not exist` | POSTGRES_USER が違う | `.env` の `POSTGRES_USER` を確認 |
| テーブルが作成されない | init.sql のマウントパスが間違っている | `docker compose down -v` でボリュームを削除して再起動 |
| MLflow UI が開かない | ポート 5001 が別プロセスに使われている | `lsof -i :5001` で確認 |

---

## 11. データ置き場について

### 現在の構成（ローカル）

データは Docker Volume でローカルマシン上に永続化する。

```
Docker Volume: postgres_data
  → /var/lib/docker/volumes/inferenceeye_postgres_data/
```

コンテナを削除（`docker compose down`）してもデータは残る。
ボリュームごと削除する場合は `docker compose down -v`（注意: データ全消去）。

### データ規模の見込み

| テーブル | 想定行数（10年分） |
|---------|----------------|
| races | 約 120,000 行 |
| entries / results | 約 180万 行 |
| odds | 約 2,000万 行（毎分取得のため） |
| training_times | 約 300万 行 |

合計数 GB 程度。ローカルの PostgreSQL で十分に扱える規模。

### クラウド移行の可能性

当面はローカルで運用する。将来的に移行が必要になった場合：

| クラウド | DB | スクレイパー実行環境 |
|---------|----|----------------|
| AWS | RDS (PostgreSQL) | EC2 |
| GCP | Cloud SQL | Cloud Run / GCE |

`DATABASE_URL` を環境変数で管理しているため、
移行時は `.env` の接続先を変えるだけで対応できる設計になっている。

---

## 12. 関連ドキュメント

- `InferenceEye_design.md` — 全体設計（ローカルのみ）
- [DB スキーマ定義書・ER 図](./db-schema.md) — テーブル定義・依存関係
- `docs/decisions/001-docker-compose.md` — Docker Compose を選んだ理由（作業中に作成）
- `notes/learnings/docker.md` — 作業中に学んだことを記録
