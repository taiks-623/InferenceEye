"""DB 接続・共通クエリ"""

import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras


@contextmanager
def get_conn():
    """PostgreSQL 接続のコンテキストマネージャ。
    正常終了時は commit、例外発生時は rollback する。
    """
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def race_exists(conn, race_id: str) -> bool:
    """race_id が races テーブルに存在するか確認する。"""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM races WHERE race_id = %s", (race_id,))
        return cur.fetchone() is not None


def upsert_race_calendar(conn, held_date: str, is_scheduled: bool) -> None:
    """race_calendars テーブルに upsert する。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO race_calendars (held_date, is_scheduled)
            VALUES (%s, %s)
            ON CONFLICT (held_date) DO UPDATE SET is_scheduled = EXCLUDED.is_scheduled
            """,
            (held_date, is_scheduled),
        )


def upsert_jockey(conn, jockey_id: str, jockey_name: str, belong_to: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO jockeys (jockey_id, jockey_name, belong_to)
            VALUES (%s, %s, %s)
            ON CONFLICT (jockey_id) DO NOTHING
            """,
            (jockey_id, jockey_name, belong_to),
        )


def upsert_trainer(conn, trainer_id: str, trainer_name: str, belong_to: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO trainers (trainer_id, trainer_name, belong_to)
            VALUES (%s, %s, %s)
            ON CONFLICT (trainer_id) DO NOTHING
            """,
            (trainer_id, trainer_name, belong_to),
        )


def upsert_horse(conn, horse: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO horses (
                horse_id, horse_name, sex, coat_color, birthday,
                father_id, mother_id, trainer_id, owner, breeder
            )
            VALUES (
                %(horse_id)s, %(horse_name)s, %(sex)s, %(coat_color)s, %(birthday)s,
                %(father_id)s, %(mother_id)s, %(trainer_id)s, %(owner)s, %(breeder)s
            )
            ON CONFLICT (horse_id) DO NOTHING
            """,
            horse,
        )


def insert_race(conn, race: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO races (
                race_id, held_date, venue_code, race_num, race_name,
                course_type, distance, direction, track_cond, weather,
                race_class, age_cond, sex_cond, weight_type, num_horses, prize_1st
            )
            VALUES (
                %(race_id)s, %(held_date)s, %(venue_code)s, %(race_num)s, %(race_name)s,
                %(course_type)s, %(distance)s, %(direction)s, %(track_cond)s, %(weather)s,
                %(race_class)s, %(age_cond)s, %(sex_cond)s, %(weight_type)s, %(num_horses)s, %(prize_1st)s
            )
            ON CONFLICT (race_id) DO NOTHING
            """,
            race,
        )


def insert_entry(conn, entry: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO entries (
                race_id, horse_num, gate_num, horse_id, jockey_id, trainer_id,
                burden_weight, horse_weight, weight_diff, scratch
            )
            VALUES (
                %(race_id)s, %(horse_num)s, %(gate_num)s, %(horse_id)s, %(jockey_id)s, %(trainer_id)s,
                %(burden_weight)s, %(horse_weight)s, %(weight_diff)s, %(scratch)s
            )
            ON CONFLICT (race_id, horse_num) DO NOTHING
            """,
            entry,
        )


def upsert_entry(conn, entry: dict) -> None:
    """entries テーブルに upsert する（出馬表取得時に使用）。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO entries (
                race_id, horse_num, gate_num, horse_id, jockey_id, trainer_id,
                burden_weight, horse_weight, weight_diff, scratch
            )
            VALUES (
                %(race_id)s, %(horse_num)s, %(gate_num)s, %(horse_id)s, %(jockey_id)s, %(trainer_id)s,
                %(burden_weight)s, %(horse_weight)s, %(weight_diff)s, %(scratch)s
            )
            ON CONFLICT (race_id, horse_num) DO UPDATE SET
                gate_num = EXCLUDED.gate_num,
                horse_id = EXCLUDED.horse_id,
                jockey_id = EXCLUDED.jockey_id,
                trainer_id = EXCLUDED.trainer_id,
                burden_weight = EXCLUDED.burden_weight,
                scratch = EXCLUDED.scratch
            """,
            entry,
        )


def upsert_training_time(conn, training: dict) -> None:
    """training_times テーブルに upsert する。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO training_times (
                horse_id, training_date, venue_code, course_type,
                time_4f, time_3f, time_1f, rank, jockey_rider, note
            )
            VALUES (
                %(horse_id)s, %(training_date)s, %(venue_code)s, %(course_type)s,
                %(time_4f)s, %(time_3f)s, %(time_1f)s, %(rank)s, %(jockey_rider)s, %(note)s
            )
            ON CONFLICT (horse_id, training_date, course_type) DO UPDATE SET
                venue_code = EXCLUDED.venue_code,
                time_4f = EXCLUDED.time_4f,
                time_3f = EXCLUDED.time_3f,
                time_1f = EXCLUDED.time_1f,
                rank = EXCLUDED.rank,
                jockey_rider = EXCLUDED.jockey_rider,
                note = EXCLUDED.note
            """,
            training,
        )


def insert_odds(conn, odds: dict) -> None:
    """odds テーブルに INSERT する（fetched_at ごとにスナップショット保存）。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO odds (race_id, horse_num, odds_type, odds_low, odds_high, fetched_at)
            VALUES (%(race_id)s, %(horse_num)s, %(odds_type)s, %(odds_low)s, %(odds_high)s, %(fetched_at)s)
            ON CONFLICT (race_id, horse_num, odds_type, fetched_at) DO NOTHING
            """,
            odds,
        )


def insert_result(conn, result: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO results (
                race_id, horse_num, finish_pos, finish_status,
                time_sec, margin, passing_order, last_3f, win_odds, popularity
            )
            VALUES (
                %(race_id)s, %(horse_num)s, %(finish_pos)s, %(finish_status)s,
                %(time_sec)s, %(margin)s, %(passing_order)s, %(last_3f)s, %(win_odds)s, %(popularity)s
            )
            ON CONFLICT (race_id, horse_num) DO NOTHING
            """,
            result,
        )
