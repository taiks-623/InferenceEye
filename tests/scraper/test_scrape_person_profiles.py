"""scrape_person_profiles.py のユニットテスト（フィクスチャ HTML・ネットワーク接続不要）"""

from bs4 import BeautifulSoup

from scraper.scrape_person_profiles import parse_belong_to


def _make_profile_soup(txt_01_content: str) -> BeautifulSoup:
    html = f"""
    <html><body>
    <p class="txt_01">{txt_01_content}</p>
    </body></html>
    """
    return BeautifulSoup(html, "lxml")


def test_parse_belong_to_jockey_bracket_format():
    """騎手ページの [美浦] 形式を正しく取得できる。"""
    soup = _make_profile_soup("1998/12/22\n[美浦]&nbsp;鈴木伸尋")
    assert parse_belong_to(soup) == "美浦"


def test_parse_belong_to_trainer_plain_format():
    """調教師ページのプレーンテキスト形式（美浦）を正しく取得できる。"""
    soup = _make_profile_soup("1970/02/24\n美浦<a href=''>師名</a>")
    assert parse_belong_to(soup) == "美浦"


def test_parse_belong_to_ritto():
    """栗東所属を正しく取得できる。"""
    soup = _make_profile_soup("1985/06/10\n[栗東]&nbsp;田中誠二")
    assert parse_belong_to(soup) == "栗東"


def test_parse_belong_to_no_txt_01():
    """p.txt_01 が存在しない場合は None を返す。"""
    soup = BeautifulSoup("<html><body><p>その他</p></body></html>", "lxml")
    assert parse_belong_to(soup) is None


def test_parse_belong_to_unknown_location():
    """既知のキーワードが含まれない場合は None を返す。"""
    soup = _make_profile_soup("1990/01/01\n不明所属")
    assert parse_belong_to(soup) is None
