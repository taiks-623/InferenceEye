-- =============================================================
-- InferenceEye DB 初期化スクリプト
-- テーブル作成順序: 外部キーの依存関係に従って定義する
-- =============================================================

-- 1. venues（競馬場マスタ）
CREATE TABLE IF NOT EXISTS venues (
    venue_code  TEXT PRIMARY KEY,
    venue_name  TEXT NOT NULL
);

-- 2. jockeys（騎手マスタ）
CREATE TABLE IF NOT EXISTS jockeys (
    jockey_id   TEXT PRIMARY KEY,
    jockey_name TEXT NOT NULL,
    belong_to   TEXT  -- '関東' / '関西' / '地方' / '外国'
);

-- 3. trainers（調教師マスタ）
CREATE TABLE IF NOT EXISTS trainers (
    trainer_id   TEXT PRIMARY KEY,
    trainer_name TEXT NOT NULL,
    belong_to    TEXT
);

-- 4. horses（馬マスタ）※ trainers に依存・father_id/mother_id は自己参照
CREATE TABLE IF NOT EXISTS horses (
    horse_id    TEXT PRIMARY KEY,
    horse_name  TEXT NOT NULL,
    sex         TEXT,        -- '牡' / '牝' / 'セ'
    coat_color  TEXT,
    birthday    DATE,
    father_id   TEXT REFERENCES horses,
    mother_id   TEXT REFERENCES horses,
    trainer_id  TEXT REFERENCES trainers,
    owner       TEXT,
    breeder     TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 5. races（レース基本情報）※ venues に依存
CREATE TABLE IF NOT EXISTS races (
    race_id       TEXT PRIMARY KEY,  -- 例: 202305010101
    held_date     DATE NOT NULL,
    venue_code    TEXT REFERENCES venues,
    race_num      INT,
    race_name     TEXT,
    course_type   TEXT,   -- '芝' / 'ダート'
    distance      INT,
    direction     TEXT,   -- '右' / '左' / '直線'
    track_cond    TEXT,   -- '良' / '稍重' / '重' / '不良'
    weather       TEXT,
    race_class    TEXT,
    age_cond      TEXT,
    sex_cond      TEXT,
    weight_type   TEXT,   -- '馬齢' / 'ハンデ' / '別定'
    num_horses    INT,
    prize_1st     INT,    -- 1着賞金（万円）
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- 6. entries（出走馬エントリー）※ races / horses / jockeys / trainers に依存
CREATE TABLE IF NOT EXISTS entries (
    race_id       TEXT REFERENCES races,
    horse_num     INT,
    gate_num      INT,
    horse_id      TEXT REFERENCES horses,
    jockey_id     TEXT REFERENCES jockeys,
    trainer_id    TEXT REFERENCES trainers,
    burden_weight FLOAT,
    horse_weight  INT,
    weight_diff   INT,
    scratch       BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (race_id, horse_num)
);

-- 7. results（レース結果）※ entries に依存
CREATE TABLE IF NOT EXISTS results (
    race_id        TEXT,
    horse_num      INT,
    finish_pos     INT,
    finish_status  TEXT,   -- '完走' / '除外' / '中止' / '失格'
    time_sec       FLOAT,
    margin         TEXT,
    passing_order  TEXT,   -- "3-3-2-1" 形式
    last_3f        FLOAT,
    win_odds       FLOAT,
    popularity     INT,
    PRIMARY KEY (race_id, horse_num),
    FOREIGN KEY (race_id, horse_num) REFERENCES entries(race_id, horse_num)
);

-- 8. odds（オッズスナップショット）※ races に依存
CREATE TABLE IF NOT EXISTS odds (
    race_id     TEXT REFERENCES races,
    horse_num   INT,
    odds_type   TEXT,         -- 'win'（単勝）/ 'place'（複勝）
    odds_low    FLOAT,
    odds_high   FLOAT,        -- 複勝は下限/上限、単勝は NULL
    fetched_at  TIMESTAMPTZ,
    PRIMARY KEY (race_id, horse_num, odds_type, fetched_at)
);

-- 9. training_times（調教タイム）※ horses に依存
CREATE TABLE IF NOT EXISTS training_times (
    horse_id      TEXT REFERENCES horses,
    training_date DATE,
    venue_code    TEXT,   -- '栗東' / '美浦' など調教場（外部キーなし）
    course_type   TEXT,   -- '坂路' / 'CW' / 'DP' / '芝'
    time_4f       FLOAT,
    time_3f       FLOAT,
    time_1f       FLOAT,
    rank          TEXT,
    jockey_rider  TEXT,
    note          TEXT,
    PRIMARY KEY (horse_id, training_date, course_type)
);

-- 10. track_bias_log（トラックバイアスログ）※ venues に依存
CREATE TABLE IF NOT EXISTS track_bias_log (
    held_date          DATE,
    venue_code         TEXT REFERENCES venues,
    course_type        TEXT,
    front_bias_score   FLOAT,
    inner_bias_score   FLOAT,
    fast_track_score   FLOAT,
    sample_count       INT,
    PRIMARY KEY (held_date, venue_code, course_type)
);

-- 11. ai_assessments（AI掲示板評価）※ races に依存
CREATE TABLE IF NOT EXISTS ai_assessments (
    race_id      TEXT REFERENCES races,
    horse_num    INT,
    source       TEXT,   -- 'netkeiba_bbs'
    summary      TEXT,
    sentiment    TEXT,   -- 'positive' / 'neutral' / 'negative'
    confidence   FLOAT,
    assessed_at  TIMESTAMPTZ,
    PRIMARY KEY (race_id, horse_num, source)
);

-- 12. predictions（予測結果）※ races に依存
CREATE TABLE IF NOT EXISTS predictions (
    race_id       TEXT REFERENCES races,
    horse_num     INT,
    predicted_at  TIMESTAMPTZ,
    win_proba     FLOAT,
    place_proba   FLOAT,
    win_ev        FLOAT,
    place_ev      FLOAT,
    ai_warning    TEXT,
    PRIMARY KEY (race_id, horse_num, predicted_at)
);

-- 13. race_calendars（開催カレンダー）※ 独立
CREATE TABLE IF NOT EXISTS race_calendars (
    held_date     DATE PRIMARY KEY,
    is_scheduled  BOOLEAN DEFAULT FALSE
);

-- =============================================================
-- 初期データ: venues（JRA 10場）
-- =============================================================
INSERT INTO venues (venue_code, venue_name) VALUES
    ('01', '札幌'),
    ('02', '函館'),
    ('03', '福島'),
    ('04', '新潟'),
    ('05', '東京'),
    ('06', '中山'),
    ('07', '中京'),
    ('08', '京都'),
    ('09', '阪神'),
    ('10', '小倉')
ON CONFLICT DO NOTHING;
