# Wikipediaから駅データを取得し、マスターデータを作るプログラム
# !pip install requests beautifulsoup4


import time
import json
import re
import requests
from bs4 import BeautifulSoup
import datetime
import os
import copy
import urllib.parse  # 追加：URLを解析するためのモジュール

#自治体データキャッシュ用
municipality_cache = {}

# 路線名データキャッシュ用
line_name_cache = {}

# リンク先の正式タイトル（H1）解決用のキャッシュ
wiki_title_cache = {}


# リンク先のURLからリダイレクトを解決して、正式なH1タイトルを取得する共通関数
def get_official_wiki_title(href, fallback_name):
    if not href or '/wiki/' not in href:
        return fallback_name
        
    url = urllib.parse.urljoin("https://ja.wikipedia.org", href)
    
    # 既に調べたことのある路線・会社ならキャッシュを即座に返す
    if url in wiki_title_cache:
        return wiki_title_cache[url]
        
    try:
        res = requests.get(url, headers={"User-Agent": "EkiDleBot/1.0"}, timeout=5)
        soup = BeautifulSoup(res.text, "html.parser")
        
        # H1タグ内のテキストを抽出（入れ子になっていても正確に抜けます）
        h1_span = soup.find('span', class_='mw-page-title-main')
        if h1_span:
            title = h1_span.get_text(strip=True)
        else:
            h1 = soup.find('h1', id='firstHeading')
            title = h1.get_text(strip=True) if h1 else fallback_name
            
        wiki_title_cache[url] = title
        time.sleep(0.5) # サーバー負荷軽減
        return title
    except Exception as e:
        print(f"      [警告] リンク先のタイトル取得に失敗: {fallback_name} ({e})")
        wiki_title_cache[url] = fallback_name
        return fallback_name


# =========================================================================
# URLの表記揺れ（エンコード）を解消して標準化する関数
# =========================================================================
def normalize_wikipedia_url(url_string):
    if not url_string:
        return ""
    # 「%E5%90%8D...」のようなエンコードを生の「名古屋市」などの日本語に直す
    decoded = urllib.parse.unquote(url_string)
    # 前後の余計な空白などを削除して返す
    return decoded.strip()

# =========================================================================
#   Wikipedia APIを使用して、リダイレクト解決後の正式な記事名を取得する関数
#   失敗時は自動でリトライし、最終的にダメならレポートリストに記録する。
# =========================================================================
failed_wiki_links = []       # タイトル取得に失敗した路線・事業者

def get_official_wiki_title_api(href, fallback_name, station_name):

    if not href or '/wiki/' not in href:
        return fallback_name
    
    # URLからタイトル部分だけを切り出してデコード（例: "/wiki/津軽鉄道津軽鉄道線" -> "津軽鉄道津軽鉄道線"）
    raw_title = urllib.parse.unquote(href.split('/wiki/')[-1])
    
    # 既に調べたことがあれば即座に返す（数千回の通信を数百回に減らす超重要処理）
    if raw_title in wiki_title_cache:
        return wiki_title_cache[raw_title]
    
    # ★ 改善1: 連絡先を含めたちゃんとしたUser-Agentを名乗ることで、APIの制限枠が大幅に緩和されます
    headers = {"User-Agent": "EkiDleBot/1.0 (contact: info@gmail.com)"}
    
    api_url = "https://ja.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "titles": raw_title,
        "redirects": "1",  # 1を指定することで、リダイレクトを自動解決させる
        "format": "json"
    }
    
    try:
        res = requests.get(api_url, params=params, headers=headers, timeout=5)
        # 429等のエラーなら例外を発生させて即座に第2段階（HTML）へ逃げる
        res.raise_for_status() 
        data = res.json()
        
        pages = data.get("query", {}).get("pages", {})
        if pages:
            page_info = list(pages.values())[0]
            if "missing" not in page_info:
                true_title = page_info.get("title", fallback_name)
                wiki_title_cache[raw_title] = true_title
                time.sleep(0.1)
                return true_title
                
    except Exception as e:
        # APIが失敗（429など）した場合は、エラーログを出して直ちにHTML方式へ移行
        print(f"      [API拒否] {raw_title} ({e}) ➔ HTML方式に切り替えます")

    # --- 第2段階：制限の緩いHTML通信で確実に取得（リカバリー） ---
    # APIが弾かれた直後なので、サーバーを刺激しないよう少し待つ
    time.sleep(3.0) 
    url = urllib.parse.urljoin("https://ja.wikipedia.org", href)
    
    try:
        res_html = requests.get(url, headers=headers, timeout=10)
        res_html.raise_for_status()
        soup = BeautifulSoup(res_html.text, "html.parser")
        
        h1_span = soup.find('span', class_='mw-page-title-main')
        if h1_span:
            title = h1_span.get_text(strip=True)
        else:
            h1 = soup.find('h1', id='firstHeading')
            title = h1.get_text(strip=True) if h1 else fallback_name
            
        wiki_title_cache[raw_title] = title
        print(f"      [リカバリー成功] HTML通信で {title} を取得しました。")
        time.sleep(2.0) # HTML取得後はしっかり休ませる
        return title
        
    except Exception as e:
        # HTMLすら拒否された場合の最終手段
        print(f"      [完全失敗] HTMLでの取得にも失敗: {fallback_name} ({e})")
        wiki_title_cache[raw_title] = fallback_name
        failed_wiki_links.append(f"{station_name} : {fallback_name}")   # レポートリストに追加
        return fallback_name


# =========================================================================
# 都市カテゴリJSONの読み込みと判定用セット（URLベース）の作成
# =========================================================================
DESIGNATED_CITIES_URLS = set()
CORE_CITIES_URLS = set()
SPECIAL_CITIES_URLS = set()
PREF_CAPITALS_URLS = set()
CITY_URL_MAP = {} # 市名からURLを逆引きするための辞書

if os.path.exists("city_categories.json"):
    try:
        with open("city_categories.json", "r", encoding="utf-8") as f:
            city_categories_data = json.load(f)
            # URL全体をデコード（標準化）してセットに格納
            DESIGNATED_CITIES_URLS = set(normalize_wikipedia_url(item["url"]) for item in city_categories_data.get("政令指定都市", []))
            CORE_CITIES_URLS = set(normalize_wikipedia_url(item["url"]) for item in city_categories_data.get("中核市", []))
            SPECIAL_CITIES_URLS = set(normalize_wikipedia_url(item["url"]) for item in city_categories_data.get("施行時特例市", []))
            PREF_CAPITALS_URLS = set(normalize_wikipedia_url(item["url"]) for item in city_categories_data.get("都道府県庁所在地", []))
            for category_list in city_categories_data.values():
                for item in category_list:
                    CITY_URL_MAP[item["name"]] = item["url"]
    except Exception as e:
        print(f"city_categories.json の読み込みに失敗しました: {e}")


# =========================================================================
# uub.jpから最新の全国市区町村リストを全自動で取得する関数
# =========================================================================
# def fetch_all_municipalities():
#     url = "https://uub.jp/ctv/search.cgi?L=kanni&Pa=全国&B=最新&C=1&T=1&V=1&U=1&Dn=1&Dp=1&Dj=1&Da=1&Dm=1&R=stan&A=all&M=part"
#     headers = {
#         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
#     }
#     try:
#         print("uub.jp から最新の市区町村リストを取得しています...")
#         response = requests.get(url, headers=headers, timeout=10)
#         response.raise_for_status()
#        response.encoding = response.apparent_encoding
#         soup = BeautifulSoup(response.text, 'html.parser')

#         municipality_tags = soup.find_all('td', class_=['bcity', 'btown', 'bvill'])
#         municipalities = [tag.get_text(strip=True) for tag in municipality_tags]
#         return municipalities
#     except Exception as e:
#         print(f"市区町村リストの取得に失敗しました: {e}")
#         # 万が一uub.jpが落ちていた時のための最低限のセーフティネット（上郡町・平群町）
#         return ['上郡町', '平群町']



# =========================================================================
# 自治体ページから「都道府県」だけをピンポイントで抜く関数
# =========================================================================
# レポート出力用リスト
url_completed_stations = []  # URLを補完した駅

def extract_municipality_data(html_text):
    """
    自治体のWikipediaページから、人口や面積などの詳細データを抽出する関数
    """
    soup = BeautifulSoup(html_text, 'html.parser')

    data = {
        "region": [],               # 地方
        "prefecture": "",           # 都道府県
        "muni_type": "",            # 自治体の種類（市、区、町、村）
        "name_kanji_len": 0,        # 固有名称の表記文字数
        "name_yomi_len": 0,         # 固有名称の読み文字数
        "city_code": "",            # 市町村コード
        "corporate_number": "",     # 法人番号
        "area": None,               # 面積（数字のみ）
        "population": None,         # 総人口（数字のみ）
        "population_density": None, # 人口密度（数字のみ）
        "first_paragraph": "",      # 冒頭の1段落
        "postal_code": ""           # 所在地の郵便番号
    }

    kanji_raw = ""
    yomi_raw = ""

    h1 = soup.find('h1', id='firstHeading')
    if h1:
        kanji_raw = re.sub(r'\s*\(.*?\)', '', h1.get_text(strip=True))

    infobox = soup.find('table', class_='infobox')
    if infobox:
        th_first = infobox.find('th')
        if th_first:
            for s in th_first.stripped_strings:
                if re.match(r'^[ぁ-んァ-ヶー]+(?:し|く|まち|ちょう|むら|そん)$', s):
                    yomi_raw = s
                    break

        for tr in infobox.find_all('tr'):
            th = tr.find('th')
            td = tr.find('td')
            if not th or not td:
                continue

            header = th.get_text(strip=True)
            # 注釈（[1]など）のリンクを完全に排除してからテキスト化
            td_copy = copy.copy(td)
            for sup in td_copy.find_all('sup'):
                sup.decompose()
            value = td_copy.get_text(" ", strip=True)

            if header == '地方':
                # 変更点1：value.replace(' ', '') を廃止し、区切りのスペースや改行をそのまま活かす
                # 変更点2：正規表現を「+? (最短一致)」にして、「北陸地方甲信越地方」と繋がるのを防ぐ
                regions = re.findall(r'[一-龠ぁ-んァ-ヶ]+?地方', value)

                # 重複を排除して配列（リスト）として格納する
                if regions:
                    data["region"] = list(dict.fromkeys(regions))

            elif header == '都道府県':
                # 全角「（）」および半角「()」で囲まれた部分（振興局など）を中身ごと完全に削除
                clean_pref = re.sub(r'[（\(]\s*.*?\s*[）\)]', '', value).strip()
                data["prefecture"] = clean_pref
            elif header == '都道府県':
                data["prefecture"] = value
            elif 'コード' in header:
                m = re.search(r'\d{5}-\d?', value)
                if m: data["city_code"] = m.group(0)
            elif header == '法人番号':
                m = re.search(r'\d+', value.replace(',', ''))
                if m: data["corporate_number"] = m.group(0)
            elif header == '面積':
                m = re.search(r'[\d\.]+', value.replace(',', ''))
                if m: data["area"] = float(m.group(0))
            elif header == '総人口' or header == '人口':
                m = re.search(r'\d+', value.replace(',', ''))
                if m: data["population"] = int(m.group(0))
            elif header == '人口密度':
                m = re.search(r'\d+', value.replace(',', ''))
                if m: data["population_density"] = int(m.group(0))
            elif header == '所在地':
                m = re.search(r'〒?\s*(\d{3}-\d{4})', value)
                if m: data["postal_code"] = m.group(1)

    # 文字数と種類の計算（大町町・大村市などの文字重複も正確に処理）
    if kanji_raw:
        match = re.search(r'(市|区|町|村)$', kanji_raw)
        if match:
            data["muni_type"] = match.group(1)
            data["name_kanji_len"] = len(kanji_raw) - 1

    if yomi_raw:
        if yomi_raw.endswith(('まち', 'むら', 'そん')):
            data["name_yomi_len"] = len(yomi_raw) - 2
        elif yomi_raw.endswith('ちょう'):
            data["name_yomi_len"] = len(yomi_raw) - 3
        elif yomi_raw.endswith(('し', 'く')):
            data["name_yomi_len"] = len(yomi_raw) - 1

    # 冒頭1段落の抽出（注釈の完全削除）
    content_div = soup.find("div", class_="mw-parser-output")
    if content_div:
        for p in content_div.find_all('p', recursive=False):
            if p.get('class') and 'mw-empty-elt' in p.get('class'):
                continue
            text = p.get_text(strip=True)
            if len(text) > 10:
                p_copy = copy.copy(p)
                for sup in p_copy.find_all('sup'):
                    sup.decompose()
                data["first_paragraph"] = p_copy.get_text(strip=True)
                break

    return data

BASE_INDEX_URL = "https://ja.wikipedia.org/wiki/日本の鉄道駅一覧"

# 万が一自動取得に失敗したときのためのバックアップリスト
BACKUP_SUB_PAGES = ["あ", "い", "う", "え", "お", "か", "き", "く", "け", "こ",
                 "さ", "し", "しや-しん", "す", "せ", "そ", "た", "ち", "つ", "て", "と",
                 "な", "に", "ぬ", "ね", "の", "は", "ひ", "ふ", "へ", "ほ",
                 "ま", "み", "む", "め", "も", "や", "ゆ", "よ", "ら", "り",
                 "る", "れ", "ろ", "わ", "を", "ん"]

def get_todays_sub_pages():
    print("Wikipediaトップページから最新のページ構成を自動取得しています...")
    sub_pages_all = []

    try:
        res = requests.get(BASE_INDEX_URL, headers={"User-Agent": "EkiDleBot/1.0"})
        soup = BeautifulSoup(res.text, "html.parser")

        for a in soup.find_all("a", href=True):
            href = urllib.parse.unquote(a["href"])
            # 検索条件を少し緩くして、確実にリンクを拾う
            if "/wiki/日本の鉄道駅一覧_" in href:
                page_name = href.split("日本の鉄道駅一覧_")[-1].split("#")[0]
                if page_name and "?" not in page_name and page_name not in sub_pages_all:
                    sub_pages_all.append(page_name)
    except Exception as e:
        print(f"自動取得中にエラーが発生しました: {e}")

    # 【重要】万が一、リストが空っぽ（0件）になってしまったらバックアップを使う安全装置
    if not sub_pages_all:
        print("⚠ ページ構成の自動取得に失敗したため、バックアップのリストを使用します。")
        sub_pages_all = BACKUP_SUB_PAGES
    else:
        print(f"正しいページインデックス（全{len(sub_pages_all)}ページ）を取得しました。")

    weekday = datetime.datetime.today().weekday()

    # ★動作テストのため、強制的に「6:日曜日」に設定します
    weekday = 0

    # 取得した全ページを曜日ごとに自動で7等分する
    chunks = [[] for _ in range(7)]
    for i, page in enumerate(sub_pages_all):
        chunks[i % 7].append(page)

    # 万が一、分割後も箱が空だった場合のエラー回避
    if not chunks[weekday]:
        chunks[weekday] = ["あ"]

    # ★テスト用に「あ」のページだけを強制的に指定します
    # return ["む-も", "や-わ行"], weekday

    # ★手動更新用に全ページ（sub_pages_all）を強制的に返すようにします
    # 本来の挙動に戻すときは、これをコメントアウトして下のreturn chunks[weekday], weekdayを有効化
    #return sub_pages_all, weekday

    return chunks[weekday], weekday


# =========================================
# 駅のWikipediaページから各種データを抽出し、自治体データと統合する関数
# =========================================

def fetch_station_details(url, station_name):

    data = {
        "pref": "",                # 都道府県名
        "county": "",              # 郡名
        "municipality": "",        # 市区町村名
        "ward": "",                # 政令指定都市の行政区名
        "muni_url": "",            # 自治体ページのURL
        "is_designated_city": False, # 政令指定都市か
        "is_core_city": False,       # 中核市か
        "is_special_city": False,    # 施行時特例市か
        "is_pref_capital": False,    # 都道府県庁所在地か
        "platforms": 0,            # 面数
        "tracks": 0,               # 線路数
        "min_km": float('inf'),    # 最小営業キロ
        "open_year": None,         # 開業年
        "open_month": None,        # 開業月
        "open_day": None,          # 開業日
        "max_passengers": 0,       # 最大乗降客数
        "companies": [],           # 所属事業者
        "lines": [],               # 乗入路線
        "romaji": "",              # アルファベット表記（最初のinfoboxのみ）
        "address": "",             # 住所（所在地）
        "latitude": None,          # 緯度
        "longitude": None,         # 経度
        "adjacent_stations": [],   # 隣駅リスト（辞書形式: name, link）
        "transfer_stations": [],    # 乗換駅リスト（辞書形式: name, link）
        "is_abolished_confirmed": False # ★追加：確実に廃止された証拠を掴んだかどうかのフラグ
    }

    try:
        res = requests.get(url, headers={"User-Agent": "EkiDleBot/1.0"}, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")

        infoboxes = soup.find_all('table', class_='infobox')

        # ↓↓↓ ここから追加 ↓↓↓
        # 廃止駅確認処理
        active_infoboxes = []
        abolished_count = 0 # ★追加：廃止と書かれた表の数をカウント

        # 【追加】今日の日付をYYYYMMDDの整数で取得（未来の廃止予定日を保護するため）
        today_int = int(datetime.datetime.today().strftime('%Y%m%d'))
        
        for box in infoboxes:
            is_abolished = False

            # --- 判定用の変数を準備 ---
            has_active_sign = False   # 現役の証拠（乗客データ等）
            max_open_date = 0         # 一番新しい開業・再開業年月日
            max_abolished_date = 0    # 一番新しい廃止年月日

            # 取得した表の行(tr)を1行ずつ確認する
            for tr in box.find_all('tr'):
                th = tr.find('th') # 見出し
                td = tr.find('td') # 中身

                if th and td:
                    th_text = th.get_text(strip=True)
                    td_text = td.get_text(strip=True)
                    
                    # 日付（YYYYMMDD）を抽出する内部関数
                    def extract_max_date(text, current_max):
                        matches = re.finditer(r'(\d{4})年(?:.*?(\d{1,2})月)?(?:.*?(\d{1,2})日)?', text)
                        for m in matches:
                            y = int(m.group(1))
                            mo = int(m.group(2)) if m.group(2) else 1
                            d = int(m.group(3)) if m.group(3) else 1
                            date_int = y * 10000 + mo * 100 + d
                            if date_int > current_max:
                                current_max = date_int
                        return current_max

                    # ① 廃止の記述があるかチェック
                    if '廃止' in th_text and td_text:
                        is_abolished = True
                        max_abolished_date = extract_max_date(td_text, max_abolished_date)
                    
                    # ② 開業・再開業の記述がある場合
                    # 【追加】「再開業」「経営移管」などの復活キーワードも逃さず抽出
                    if any(word in th_text for word in ['開業', '再開業', '経営移管', '再開', '営業再開']) and td_text:
                        max_open_date = extract_max_date(td_text, max_open_date)
                            
                    # ③ 現役旅客駅の証拠（乗降人員があること）
                    if ('乗車人員' in th_text or '乗降人員' in th_text) and td_text:
                        has_active_sign = True

            # --- 最終判定（究極のセーフティネット） ---
            if is_abolished:
                # パターンA: 新しい形で開業した（一番新しい開業日が、廃止日以降）
                if max_open_date >= max_abolished_date and max_open_date > 0:
                    is_abolished = False
                    
                # パターンB: 廃止予定日が「今日より未来」である（まだ生きている）
                # 秋田港駅のような「数日後に廃止される予定の駅」をギリギリまで現役として保護します
                elif max_abolished_date > today_int:
                    is_abolished = False
                    
                # パターンC: 「廃止」の文字はあったが明確な日付がなく、現役の証拠がある
                # （例: 「1984年貨物取扱廃止」だけでis_abolishedがTrueになってしまった現役旅客駅）
                elif max_abolished_date == 0 and has_active_sign:
                    is_abolished = False
                    
                else:
                    abolished_count += 1
                    print("      [除外] 過去の廃止情報が含まれる表を検知したため、抽出から除外しました。")


            # 廃止フラグが立っていない（現役の）表だけをリストに追加する
            if not is_abolished:
                active_infoboxes.append(box)

        # ★追加：もし「現役の表が1つも無い」かつ「廃止の表があった」なら、これは紛れもなく廃駅！
        if not active_infoboxes and abolished_count > 0:
            data["is_abolished_confirmed"] = True

        # これ以降の処理を、現役の表（active_infoboxes）だけで行うようにデータを上書きする
        infoboxes = active_infoboxes

        if not infoboxes:
            # 現役のinfoboxが1つも無い場合は、強制的にエラーデータを返して
            # 後続の「廃駅」または「完全保護」ロジックに委ねる
            data["min_km"] = None 
            return data

        # ↓↓↓ ここから追加 ↓↓↓
        best_addr = ""
        best_lat = None
        best_lon = None
        best_loc_td = None # ★追加：最古の所在地のHTMLタグそのものを記憶する変数
        oldest_y = float('inf')

        for box in infoboxes:
            box_addr = ""
            box_lat = None
            box_lon = None
            box_loc_td = None
            box_y = None

            # まず、このInfobox内に座標タグがあるか探す
            #infoboxから座標を取得する
            geo_span = box.find('span', class_='geo')
            if geo_span:
                lat_lon = geo_span.get_text(strip=True).split(';')
                if len(lat_lon) == 2:
                    try:
                        box_lat = float(lat_lon[0].strip())
                        box_lon = float(lat_lon[1].strip())
                    except ValueError:
                        pass

            # ★追加：この箱の中に本物の「所在地」見出しがあるか事前チェック
            box_has_real_location = any(t.find('th') and t.find('th').get_text(strip=True) == '所在地' for t in box.find_all('tr'))

            #infoboxから所在地を取得する
            for tr in box.find_all('tr'):
                th = tr.find('th')
                td = tr.find('td')

                hdr = ""
                # 通常処理：見出しと中身がある場合
                if th and td:
                    hdr = th.get_text(strip=True)
                # 予備処理：見出しが無く、本物の所在地行も存在しない場合（「所在地幅=long」対応）
                elif not th and td:
                    txt = td.get_text(" ", strip=True)
                    # ⭕ 修正後（政令指定都市名を追加）
                    if not box_has_real_location and re.search(r'(東京都|北海道|(?:京都|大阪)府|[一-龠]{2,3}県|札幌市|仙台市|さいたま市|千葉市|横浜市|川崎市|相模原市|新潟市|静岡市|浜松市|名古屋市|京都市|大阪市|堺市|神戸市|岡山市|広島市|北九州市|福岡市|熊本市)', txt):
                        hdr = '所在地'

                # 判定結果（hdr）をもとにデータを抽出
                if hdr == '所在地':
                    clean = re.split(r'<div|<sup', str(td))[0]
                    txt = BeautifulSoup(clean, "html.parser").get_text(strip=True)
                    box_addr = re.split(r'\[|座標:|北緯|〒', txt)[0].strip()
                    box_loc_td = td # 所在地のtdタグを確保
                elif '開業年月日' in hdr:
                    m = re.search(r'(\d{4})年', td.get_text(strip=True))
                    if m:
                        y = int(m.group(1))
                        if box_y is None or y < box_y:
                            box_y = y

            # 最初の住所・座標を保険としてキープ
            if box_addr and not best_addr:
                best_addr = box_addr
                best_loc_td = box_loc_td
            if box_lat is not None and best_lat is None:
                best_lat = box_lat
                best_lon = box_lon

            # より古い開業年が見つかれば、そのInfoboxの住所・座標で上書き！
            if box_y is not None and box_y < oldest_y:
                oldest_y = box_y
                if box_addr:
                    best_addr = box_addr
                    best_loc_td = box_loc_td
                if box_lat is not None:
                    best_lat = box_lat
                    best_lon = box_lon

        data["address"] = best_addr
        data["latitude"] = best_lat
        data["longitude"] = best_lon
        # ↑↑↑ ここまで追加 ↑↑↑

        # （これ以降は、もともとある既存の for tr in infobox.find_all('tr'): などの処理が続きます）

        # 複数のInfoboxを1つずつ順番に処理する
        for idx, infobox in enumerate(infoboxes):

            # --- 【追加】アルファベット表記の抽出（最初のinfoboxのみ） ---
            if idx == 0:
                romaji_span = infobox.find('span', lang='en')
                if romaji_span:
                    data["romaji"] = romaji_span.get_text(strip=True)

            # --- 【修正】隣駅の抽出（リンク付き） ---
            # 「float:left」と「float:right」のdivが横並びになっている行を探す
            for tr in infobox.find_all('tr'):
                tds = tr.find_all('td')
                if len(tds) == 1 and tds[0].get('colspan') == "2":
                    left_div = tds[0].find('div', style=lambda s: s and 'float:left' in s.replace(' ', ''))
                    right_div = tds[0].find('div', style=lambda s: s and 'float:right' in s.replace(' ', ''))

                    # 左右のdivの中からリンク付きで駅名を取り出す内部関数
                    def add_adjacent(div_node):
                        if div_node:
                            for a in div_node.find_all('a'):
                                txt = a.get_text(strip=True)
                                href = a.get('href')
                                # 画像リンク（駅ナンバリングのアイコン等）を弾き、名前とリンクがあるものだけ抽出
                                if href and '/wiki/' in href and a.get('title') and txt:
                                    full_link = urllib.parse.urljoin("https://ja.wikipedia.org", href)
                                    # 既に同じ駅名が登録されていなければ追加
                                    if not any(adj["name"] == txt for adj in data["adjacent_stations"]):
                                        data["adjacent_stations"].append({"name": txt, "link": full_link})

                    add_adjacent(left_div)
                    add_adjacent(right_div)

            # --- 【追加】本物の「所在地」見出しがあるか事前チェック ---
            has_real_location = any(tr.find('th') and tr.find('th').get_text(strip=True) == '所在地' for tr in infobox.find_all('tr'))

            # ここから元の処理（<th>と<td>のペアを探す処理）が続く
            for tr in infobox.find_all('tr'):
                th = tr.find('th')
                td = tr.find('td')


                # ▼ここから不要
                # th必須にする処理は土合駅でバグを起こすため廃止
                # 見出し(th)と中身(td)のペアが揃っていない行は、すべて問答無用で無視する
                # if not th or not td:
                #     continue

                # header = th.get_text(strip=True)
                # value_text = td.get_text(" ", strip=True)
                # ▲ここまで不要

                header = ""
                value_text = ""

                if th and td:
                    header = th.get_text(strip=True)
                    value_text = td.get_text(" ", strip=True)
                elif not th and td:
                    value_text = td.get_text(" ", strip=True)
                    # 郵便番号等があってもマッチするように re.search を使用
                    # 【変更】土合駅のように本物の所在地が「存在しない」場合のみ、都道府県名を頼りに推測を発動する
                    # ⭕ 修正後（同じく政令指定都市名を追加）
                    if not has_real_location and re.search(r'(東京都|北海道|(?:京都|大阪)府|[一-龠]{2,3}県|札幌市|仙台市|さいたま市|千葉市|横浜市|川崎市|相模原市|新潟市|静岡市|浜松市|名古屋市|京都市|大阪市|堺市|神戸市|岡山市|広島市|北九州市|福岡市|熊本市)', value_text):
                        header = '所在地'
                else:
                    continue



                # ① 所在地と住所の抽出
                if header == '所在地':

                    # ▼ここから不要
                    # 古い開業年月日を取得する段階で座標も取得するようになったため不要
                    # 【シンプル化】注釈(<sup)や座標等の補足(<div)が始まる前までのHTMLを切り出してテキスト化
                    # clean_html = re.split(r'<div|<sup', str(td))[0]
                    # address_text = BeautifulSoup(clean_html, "html.parser").get_text(strip=True)
                    # 念のため、テキスト上のゴミ（[1]、座標、郵便番号など）から先を完全にカット
                    # address_text = re.split(r'\[|座標:|北緯|〒', address_text)[0].strip()

                    # このInfobox内で見つけた住所として一時保存
                    # if address_text:
                    #     local_address = address_text
                    #     if not fallback_address:
                    #        fallback_address = local_address
                    # ▲ここまで不要

                    # ★変更：現在見ている td ではなく、事前ループで勝ち残った「最古の td (best_loc_td)」を使う
                    target_td = best_loc_td if best_loc_td else td

                    # 1. 駅ページから都道府県を優先取得
                    if not data["pref"]:
                        m_pref = re.search(r'(東京都|北海道|(?:京都|大阪)府|[一-龠]{2,3}県)', target_td.get_text(strip=True))
                        if m_pref:
                            data["pref"] = m_pref.group(1)

                    # 2. 所在地内のリンクから市区町村と行政区を抽出
                    if not data.get("municipality") or not data.get("ward"):
                        for a in target_td.find_all('a'):
                            txt = a.get_text(strip=True)
                            href = a.get('href')

                            if not href or '/wiki/' not in href:
                                continue

                            is_tokyo_ku = txt.endswith('区') and (data["pref"] == '東京都' or '東京' in value_text)

                            # ★変更箇所：郡名と市区町村を独立して抽出
                            if txt.endswith('郡'):
                                if not data.get("county"):
                                    data["county"] = txt

                            # 市区町村の特定とURLの確保
                            elif txt.endswith(('市', '町', '村')) or is_tokyo_ku:
                                if not data["municipality"]:
                                    data["municipality"] = txt
                                    data["muni_url"] = urllib.parse.urljoin("https://ja.wikipedia.org", href)

                            # 政令指定都市の行政区は名前のみ抽出
                            elif txt.endswith('区'):
                                if not data["ward"]:
                                    data["ward"] = txt

                    # 3. リンクから市区町村名が取れなかった場合の予備処理
                    if not data["municipality"]:
                        loc = re.sub(r'〒?\s*\d{3}-\d{4}', '', value_text).strip()
                        loc = re.sub(r'^(?:東京都|北海道|(?:京都|大阪)府|[一-龠]{2,3}県)', '', loc).strip()
                        # 【スペース削除対応】空白を完全に消去してから判別
                        loc = loc.replace(' ', '').replace(' ', '')

                        # ★変更箇所：郡と市区町村を別々にキャプチャする
                        m = re.match(r'^((?:.+?郡)?)(.+?(?:市|区|町|村))', loc)
                        if m:
                            county_str = m.group(1)
                            muni_str = m.group(2)

                            if county_str:
                                data["county"] = county_str

                            # 大町町や野々市市などの文字重複対策
                            if loc.startswith(county_str + muni_str + muni_str[-1]):
                                muni_str += muni_str[-1]
                            if loc.startswith(county_str + muni_str + '市'):
                                muni_str += '市'

                            data["municipality"] = muni_str

                    # 市区町村名はあるがURLがない場合、JSONデータからURLを補完する(特例市以上のみ対応)
                    if data["municipality"] and not data["muni_url"]:
                        if data["municipality"] in CITY_URL_MAP:
                            data["muni_url"] = CITY_URL_MAP[data["municipality"]]
                            print(f"      [URL補完] リンクなし。city_categories.jsonから {data['municipality']} のURLを補完しました。")
                            url_completed_stations.append(f"{station_name} ({data['municipality']})")

                    # =====================================================================
                    # 【修正】URLの直接比較による都市カテゴリの判定と、偽の行政区の除外処理
                    # =====================================================================
                    # スクレイピングで取得した自治体URLをデコードして標準化
                    normalized_muni_url = normalize_wikipedia_url(data["muni_url"])

                    if normalized_muni_url:
                        # 標準化されたURL同士で直接判定（非常に堅牢です）
                        data["is_designated_city"] = normalized_muni_url in DESIGNATED_CITIES_URLS
                        data["is_core_city"]       = normalized_muni_url in CORE_CITIES_URLS
                        data["is_special_city"]    = normalized_muni_url in SPECIAL_CITIES_URLS
                        data["is_pref_capital"]    = normalized_muni_url in PREF_CAPITALS_URLS

                    # 【区の誤判定防止】
                    # すでに ward（区）が抽出されているが、親の自治体が「政令指定都市」ではない場合
                    muni_name = data["municipality"]
                    if data["ward"] and not data["is_designated_city"] and data["pref"] != "東京都":
                        print(f"      [補正] {muni_name} は政令指定都市ではないため、行政区 '{data['ward']}' を除外しました。")
                        data["ward"] = ""  # 偽の行政区をリセット
                    # =====================================================================

                    # 4. 【合体】自治体データを取得して駅データに統合
                    if data["muni_url"]:
                        if data["muni_url"] not in municipality_cache:

                            # ★変更箇所：ログ出力時に郡名と市区町村名を足して表示
                            full_muni_name = data.get("county", "") + data["municipality"]
                            print(f"    自治体詳細データを取得中: {full_muni_name}")

                            try:
                                m_res = requests.get(data["muni_url"], headers={"User-Agent": "EkiDleBot/1.0"}, timeout=10)
                                municipality_cache[data["muni_url"]] = extract_municipality_data(m_res.text)
                            except Exception:
                                municipality_cache[data["muni_url"]] = {}

                        muni_data = municipality_cache[data["muni_url"]]

                        # 都道府県が駅ページに無かった場合は自治体データから補完
                        if not data["pref"] and muni_data.get("prefecture"):
                            data["pref"] = muni_data["prefecture"]

                        # 抽出した人口や面積などを駅データに結合
                        data.update(muni_data)

                # ② ホーム面数・線路数
                elif 'ホーム' in header:
                    matches = re.findall(r'(\d+)\s*面\s*(\d+)\s*線', value_text)
                    for m, s in matches:
                        data["platforms"] = max(data["platforms"], int(m))
                        data["tracks"] = max(data["tracks"], int(s))

                # ③ キロ程
                elif 'キロ程' in header:
                    matches = re.findall(r'(\d+(?:\.\d+)?)', value_text)
                    for km_str in matches:
                        data["min_km"] = min(data["min_km"], float(km_str))

                # ④ 開業年月日
                elif '開業年月日' in header:
                    m_date = re.search(r'(\d{4})年(?:.*?(\d+)月(\d+)日)?', value_text)
                    if m_date:
                        found_year = int(m_date.group(1))
                        # 初回登録、または今回見つけた年が既存の年より古い場合のみ上書きする
                        if not data["open_year"] or found_year < data["open_year"]:
                            data["open_year"] = found_year
                            data["open_month"] = int(m_date.group(2)) if m_date.group(2) else None
                            data["open_day"] = int(m_date.group(3)) if m_date.group(3) else None

                # ⑤ 乗降人員
                elif '乗車人員' in header or '乗降人員' in header:
                    clean_val = value_text.replace(',', '')
                    matches = re.findall(r'(\d+)\s*人', clean_val)
                    for num_str in matches:
                        data["max_passengers"] = max(data["max_passengers"], int(num_str))

                # ⑥ 所属事業者
                elif '所属事業者' in header or '事業者' in header:
                    found_companies = False
                    paren_depth = 0
                    # td要素内のすべての要素とテキストを上から順番に走査
                    for node in td.descendants:
                        # テキストデータ（文字）の場合、カッコの開閉状態をカウント
                        if getattr(node, 'name', None) is None:
                            text_str = str(node)
                            paren_depth += text_str.count('（') + text_str.count('(')
                            paren_depth -= text_str.count('）') + text_str.count(')')
                            paren_depth = max(0, paren_depth)

                        # aタグ（リンク）の場合
                        elif getattr(node, 'name', None) == 'a':
                            # ★カッコに囲まれた階層（ブランド名や愛称など）にいる場合はスキップ
                            if paren_depth > 0:
                                continue

                            href = node.get('href')
                            raw_name = node.get('title')
                            
                            if href and '/wiki/' in href and raw_name:
                                found_companies = True
                                # API関数を渡して正式な会社名を取得
                                official_name = get_official_wiki_title_api(href, raw_name, station_name)

                                # ★ 末尾の曖昧さ回避（例: " (企業)" や " (鉄道)"）を全自動で削除
                                official_name = re.sub(r'\s\(.*?\)$', '', official_name)

                                if official_name not in data["companies"]:
                                    data["companies"].append(official_name)
                                
                    # リンクが一つも貼られていなかった駅のための安全装置（従来のテキスト分割）
                    if not found_companies:
                        clean_val = re.sub(r'（[^）]*）|\([^)]*\)|\[[^\]]*\]', '', value_text)
                        comps = [c.strip() for c in re.split(r'\s|・', clean_val) if c.strip()]
                        for c in comps:
                            if c and c not in data["companies"]:
                                data["companies"].append(c)

                # ⑦ 所属路線
                elif '所属路線' in header or header == '路線':
                    found_lines = False
                    paren_depth = 0

                    # td要素内のすべての要素とテキストを上から順番に走査
                    for node in td.descendants:

                        # テキストデータ（文字）の場合、カッコの開閉状態をカウント
                        if getattr(node, 'name', None) is None:
                            text_str = str(node)
                            paren_depth += text_str.count('（') + text_str.count('(')
                            paren_depth -= text_str.count('）') + text_str.count(')')
                            paren_depth = max(0, paren_depth)

                       # aタグの場合
                        if getattr(node, 'name', None) == 'a':

                            # カッコ内の愛称・直通・貨物通称などは一切無視する
                            if paren_depth > 0:
                                continue

                            # title属性（Wikipediaの正式な記事名）を取得
                            href = node.get('href')
                            raw_name = node.get('title')

                            if href and '/wiki/' in href and raw_name:
                                found_lines = True  # リンクがあったフラグ
                                # リダイレクトを解決して正式な路線名を取得
                                official_name = get_official_wiki_title_api(href, raw_name, station_name)
                                # 末尾の曖昧さ回避カッコ（例: " (鉄道)"）を全自動で削除
                                official_name = re.sub(r'\s\(.*?\)$', '', official_name)

                                # ==========================================
                                # ★ 路線名の名寄せ（エイリアス）辞書
                                # ==========================================
                                line_aliases = {
                                    "東海道線": "東海道本線",
                                    "宇都宮線": "東北本線",
                                    "JR京都線": "東海道本線",
                                    # 必要に応じてここに追記していく
                                }

                                # 最終的にきれいになった路線名をリストに追加
                                if official_name not in data["lines"]:
                                    data["lines"].append(official_name)

                    #  リンクが1つも存在しなかった場合（札幌市電山鼻西線など）の安全な抽出処理
                    # バグの原因になるため、当分廃止。路線名が全くないものについては手動で追加する。
                    # if not found_lines:
                        # 1. <sup>タグ（[1]などの注釈）をHTMLから直接削除する
                    #     td_copy = copy.copy(td)
                    #    for sup in td_copy.find_all('sup'):
                    #        sup.decompose()
                        
                        # 2. 改行（<br>など）を区切り文字としてテキストを取得
                        # これにより「・」や「（）」は路線名の一部としてそのまま残る
                    #    for br in td_copy.find_all('br'):
                    #        br.replace_with('\n')

                        # ★ separator 指定を外し、タグの境界での勝手な分割を防ぐ
                    #    fallback_text = td_copy.get_text()
                        
                        # 3. 改行ごとに分割してリストに追加
                    #    for line_text in fallback_text.split('\n'):
                            # 空白(全半角)、■◆●などの箇条書き記号、[1]などのテキスト注釈を一網打尽にする
                    #        clean_line = re.sub(r'[\s\*■◆●○◇]|\[[^\]]*\]', '', line_text).strip()
                            # 抽出した路線名を登録
                    #        if clean_line and clean_line not in data["lines"]:
                    #            data["lines"].append(clean_line)

                # ⑧ 乗換駅の名前とリンクの抽出
                elif '乗換' in header:
                    for a in td.find_all('a'):
                        txt = a.get_text(strip=True)
                        href = a.get('href')
                        # 路線名（ゆりかもめ等）が混ざるのを防ぐため、「〜駅」で終わるリンクのみを厳格に抽出する
                        if href and '/wiki/' in href and txt.endswith('駅'):
                            full_link = urllib.parse.urljoin("https://ja.wikipedia.org", href)
                            # 重複登録を防ぐ
                            if not any(ts["name"] == txt for ts in data["transfer_stations"]):
                                data["transfer_stations"].append({"name": txt, "link": full_link})
                # ↑↑↑ ここまで追加 ↑↑↑

    except Exception as e:
        # 何のエラーで失敗したか分かるように表示する
        print(f"      [警告] データ抽出中にエラーが発生しました: {e}")
        pass

    # try-except の外に安全装置を置くことで、エラー発生時でも確実に変換させる
    if data["min_km"] == float('inf'):
        data["min_km"] = None

    return data

def extract_and_count_stations():
    headers = {
        "User-Agent": "EkiDleBot/1.0"
    }

    print("Wikipediaからの駅名抽出（全文字数・例外対応版）を開始します...")

    SUB_PAGES, weekday_num = get_todays_sub_pages()
    weekdays_str = ["月", "火", "水", "木", "金", "土", "日"]
    print(f"本日は【{weekdays_str[weekday_num]}曜日】の割り当て分（{'、'.join(SUB_PAGES)}）を抽出します。")

    # 全Wikipediaサブページ一覧の取得（引っ越し判定用）
    all_wikipedia_sub_pages = []
    try:
        res_all = requests.get(BASE_INDEX_URL, headers={"User-Agent": "EkiDleBot/1.0"}, timeout=10)
        soup_all = BeautifulSoup(res_all.text, "html.parser")
        for a in soup_all.find_all("a", href=True):
            href = urllib.parse.unquote(a["href"])
            if "/wiki/日本の鉄道駅一覧_" in href:
                p_name = href.split("日本の鉄道駅一覧_")[-1].split("#")[0]
                if p_name and "?" not in p_name and p_name not in all_wikipedia_sub_pages:
                    all_wikipedia_sub_pages.append(p_name)
    except Exception:
        all_wikipedia_sub_pages = BACKUP_SUB_PAGES

    base_date = datetime.datetime(2024, 1, 1)
    today_dt = datetime.datetime.today()
    current_day_index = (today_dt - base_date).days

    updated_summary = []
    error_stations = []
    patched_stations = {}

    # -------------------------------------------------------------------------
    # 各五十音ページごとに抽出 ➔ stations.json へ即時保存するループ
    # -------------------------------------------------------------------------
    for page in SUB_PAGES:
        url = f"{BASE_INDEX_URL}_{page}"
        print(f"\n========================================")
        print(f" 読み込み中: {url}")
        print(f"========================================")

        page_stations = []

        try:
            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                print(f"ページの取得に失敗しました: {page}")
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            content_div = soup.find("div", class_="mw-content-ltr")
            if not content_div:
                continue

            li_tags = content_div.find_all("li")

            for li in li_tags:
                a_tag = li.find("a")
                if not a_tag or not a_tag.get("href"):
                    continue

                href = a_tag.get("href")
                wiki_url = urllib.parse.urljoin("https://ja.wikipedia.org", href)

                kanji_raw = a_tag.get_text()
                display_name = kanji_raw.strip()

                # ★特定の駅だけを取得したい場合は、全曜日指定にした上で、このリストの中に駅名を書きます。
                # 例: TARGET_STATIONS = ["ガーラ湯沢駅", "高輪ゲートウェイ駅"]
                # 空っぽ [] の場合は、通常通りページ内の全駅を取得します。
                TARGET_STATIONS = [
                    #"青柳町停留場", "石山通停留場", "行啓通停留場", "競馬場前停留場", "幌南小学校前停留場",
                    #"魚市場通停留場", "大町停留場 (北海道)", "柏木町停留場", "駒場車庫前停留場", "五稜郭公園前停留場",
                    #"資生館小学校前停留場", "市役所前停留場 (北海道)", "十字街停留場", "昭和橋停留場", 
                    #"新川町停留場", "末広町停留場 (北海道)", "杉並町停留場", "静修学園前停留場", "狸小路停留場", 
                    #"千歳町停留場 (北海道)", "中央区役所前停留場", "中央図書館前停留場", "中央病院前停留場",
                    #"千代台停留場", "電車事業所前停留場", "中島公園通停留場", "西15丁目停留場", "西線9条旭山公園通停留場",
                    #"西線11条停留場", "西線14条停留場", "西線16条停留場", "西8丁目停留場", "西4丁目停留場",                                    
                    #"函館アリーナ前停留場", "函館どつく前停留場", "東屯田通停留場", "東本願寺前停留場", "深堀町停留場",
                    #"宝来町停留場", "堀川町停留場", "松風町停留場", "谷地頭停留場", "山鼻9条停留場", 
                    #"山鼻19条停留場", "湯の川停留場", "湯の川温泉停留場", "ロープウェイ入口停留場",
                    #"大沢内駅", 
                    ]
                
                if TARGET_STATIONS and (display_name not in TARGET_STATIONS):
                    # ターゲット外でも「リストには存在していた」という証拠だけは残す
                    unique_key = f"{wiki_url}_{display_name}"
                    page_stations.append({"kanji": display_name, "url": wiki_url, "unique_key": unique_key, "is_sniper_skipped": True})
                    continue

                # ★ここまで

                yomi = ""
                next_node = a_tag.next_sibling

                if next_node and hasattr(next_node, 'strip'):
                    match = re.search(r"^（(.*)）", next_node.strip())
                    if match:
                        inner_text = match.group(1)

                        page_title = urllib.parse.unquote(href.split("/wiki/")[-1])
                        url_clean_name = page_title.split("_(")[0]
                        url_dots = url_clean_name.count('・')

                        display_clean_name = re.sub(r'\s*\(.*?\)', '', display_name)
                        display_dots = display_clean_name.count('・')

                        dot_count = max(url_dots, display_dots)

                        parts = inner_text.split('・')
                        valid_parts = parts[:dot_count + 1]

                        filtered_parts = [p for p in valid_parts if not re.search(r'[一-龠]', p)]

                        if not filtered_parts:
                            filtered_parts = [parts[0]]

                        yomi_raw = "・".join(filtered_parts)
                        yomi_raw = re.sub(r"(えき|ていりゅうじょう|しんごうじょう|停留場)$", "", yomi_raw)

                        yomi = re.sub(r"[^ぁ-んァ-ヶー]", "", yomi_raw)

                if not yomi:
                    yomi = re.sub(r"[^ぁ-んァ-ヶー]", "", display_name)

                if not yomi:
                    continue

                print(f"  詳細データ取得中: {display_name}")
                details = fetch_station_details(wiki_url, display_name)

                details.update({"kanji": display_name, "yomi": yomi, "url": wiki_url, "subPage": page})
                page_stations.append(details)

                time.sleep(2)

        except Exception as e:
            print(f"エラーが発生しました: {e}")
            continue  # 通信エラー時は以後の保存や廃駅判定を行わず、即座に次のページへ退避する

        # ---------------------------------------------------------------------
        # 【1ページ完了ごとに即時実行】既存の stations.json と統合して書き出し
        # ---------------------------------------------------------------------
        existing_stations = {}
        max_id = 0

        # ★★ 1. ここに「手動補正パッチ」を追加する ★★
        MANUAL_PATCH = {
            "葉多駅": {"companies": ["神戸電鉄"]},
            "日本橋駅 (大阪府)": {"latitude": 34.667228, "longitude": 135.506285},
            "近鉄日本橋駅": {"latitude": 34.667228, "longitude": 135.506285},
            "富良野駅": {"latitude": 43.346991, "longitude": 142.391724},
            "秋田港駅": {"is_excluded": True},
            "日本の鉄道路線一覧": {"is_excluded": True},
            "一ノ堰六地蔵尊駅": {"is_excluded": True},
            "清滝駅":{"is_excluded": True},
            "ケーブル比叡駅": {"is_excluded": True},
            "青函トンネル記念館駅": {"is_excluded": True},
            "体験坑道駅": {"is_excluded": True},
            "高尾山駅": {"is_excluded": True},
            "滝本駅": {"is_excluded": True},
            "千葉貨物駅": {"is_excluded": True},
            "東港駅": {"is_excluded": True},
            "御岳山駅": {"is_excluded": True},
            # 万が一、旅客駅なのに自動判定で誤って除外されてしまった場合の救済例
            # "○○駅": {"is_excluded": False},
            # 逆に、自動判定をすり抜けた貨物専用駅を手動で除外する例
            # "××駅": {"is_excluded": True}
        }

        # 1. 既存の JSON データを読み込む
        if os.path.exists("stations.json"):
            try:
                with open("stations.json", "r", encoding="utf-8") as f:
                    data = json.load(f)

                    for item in data:
                        if item.get("id") is not None and item["id"] > max_id:
                            max_id = item["id"]

                    for item in data:
                        if item.get("id") is None:
                            max_id += 1
                            item["id"] = max_id

                        if "startDay" not in item:
                            item["startDay"] = 0
                        if "endDay" not in item:
                            item["endDay"] = 999999

                        base_url = item.get("url", "")
                        kanji = item.get("kanji", "")
                        unique_key = item.get("unique_key", f"{base_url}_{kanji}")
                        item["unique_key"] = unique_key
                        existing_stations[unique_key] = item
            except json.JSONDecodeError:
                pass

        page_fetched_keys = set(f"{v['url']}_{v['kanji']}" for v in page_stations)

        # 2. 今回取得したページデータを統合
        for v in page_stations:

            # ▼▼ ここから追加：自動除外判定とパッチ適用 ▼▼
            if not v.get("is_sniper_skipped"):  # スキップされた駅は除外判定やパッチ処理をスルーする 
                v["is_excluded"] = False
                v["exclusion_reason"] = "" 
                k_name = v["kanji"]
                comps = v.get("companies", [])
                lines = v.get("lines", [])
                # URLを日本語にデコード（解読）して文字列チェックできるようにする
                decoded_url = urllib.parse.unquote(v.get("url", ""))

                # ① 駅名で除外判定（信号場、操車場など）
                if k_name.endswith(("信号場", "操車場", "信号所", "車両基地")):
                    v["is_excluded"] = True
                    v["exclusion_reason"] = "駅名（信号場など）"
                
                # ② 事業者で除外判定（貨物専用）
                elif comps and all(c in ["日本貨物鉄道", "JR貨物"] for c in comps):
                    v["is_excluded"] = True
                    v["exclusion_reason"] = "事業者（貨物専用）"
                
                # ③ 路線名で除外判定（すべての路線がケーブルカーやロープウェイ等の場合）
                elif lines:
                    ex_keywords = ("鋼索", "ロープウェイ", "ケーブル", "索道", "ゴンドラ", "貨物線", "ロープウェー")
                    if all(any(kw in line for kw in ex_keywords) for line in lines):
                        v["is_excluded"] = True
                        v["exclusion_reason"] = "路線名（ケーブル・貨物専用）"

                # ④ URLによる除外（データが空っぽ かつ 路線ページ等に転送されている（＝駅単独のページが無い）場合のみ除外）
                elif (v.get("min_km") is None and not v.get("address")) and any(kw in decoded_url for kw in ("鋼索", "ロープウェイ", "ケーブル", "索道", "ゴンドラ", "貨物線", "ロープウェー")):
                    v["is_excluded"] = True
                    v["exclusion_reason"] = "URL（データ欠落かつケーブル・貨物専用等）"

                # ⑤ 手動パッチの適用（自動判定の結果を強制的に上書きできる）
                if k_name in MANUAL_PATCH:
                    patched_items = []
                    for patch_key, patch_val in MANUAL_PATCH[k_name].items():
                        v[patch_key] = patch_val
                        patched_items.append(patch_key)
                    patched_stations[k_name] = patched_items # 修正した項目名を記録
                    
                    # 除外フラグを操作した時だけ理由を上書きする
                    if "is_excluded" in MANUAL_PATCH[k_name]:
                        v["exclusion_reason"] = "手動パッチによる指定"
                # ▲▲ ここまで追加 ▲▲


            unique_key = f"{v['url']}_{v['kanji']}"
            v["unique_key"] = unique_key

            if unique_key in existing_stations:
                old_item = existing_stations[unique_key]

                # 個別指定で除外した駅は、更新処理をスルーして既存データを維持する
                if v.get("is_sniper_skipped"):
                    continue

                if old_item.get("endDay", 999999) < current_day_index:
                    archived_key = unique_key + f"_archived_day{old_item['endDay']}"
                    existing_stations[archived_key] = old_item

                    max_id += 1
                    v["id"] = max_id
                    v["startDay"] = current_day_index
                    v["endDay"] = 999999
                    v["missingCount"] = 0
                    existing_stations[unique_key] = v
                    print(f"  [安全復活検知] 過去に廃止された駅 {v['kanji']} を新ID({max_id})で新規登録しました。")
                    continue

                preserved_id = old_item.get("id")
                preserved_start = old_item.get("startDay", 0)
                preserved_end = old_item.get("endDay", 999999)

                if old_item.get("yomi") != v["yomi"]:
                    archived_key = unique_key + f"_archived_yomi{current_day_index}"
                    old_item["endDay"] = current_day_index
                    existing_stations[archived_key] = old_item

                    max_id += 1
                    v["id"] = max_id
                    v["startDay"] = current_day_index
                    v["endDay"] = 999999
                    v["missingCount"] = 0
                    existing_stations[unique_key] = v
                    print(f"  [読みがな変更検知] {v['kanji']} の読みが変更されたため新ID({max_id})で世代交代しました。")
                    continue

                is_fetch_failed = (v["min_km"] is None and not v["address"])

                if v.get("is_abolished_confirmed"):
                    is_fetch_failed = False
                    v["endDay"] = current_day_index
                    v["subPage"] = "廃止済"
                    print(f"  [廃駅確定] Wikipediaページ内で廃止の記述を確認: {v['kanji']}")

                if is_fetch_failed:
                    existing_stations[unique_key] = old_item
                    existing_stations[unique_key]["missingCount"] = 0
                    # 【追加】完全保護で古いデータに巻き戻した後も、手動パッチの意志だけは絶対貫徹する
                    k_name = v['kanji']
                    if k_name in MANUAL_PATCH:
                        for patch_key, patch_val in MANUAL_PATCH[k_name].items():
                            existing_stations[unique_key][patch_key] = patch_val
                            # 除外フラグを操作した場合は理由も追記しておく
                            if patch_key == "is_excluded":
                                existing_stations[unique_key]["exclusion_reason"] = "手動パッチによる指定 (完全保護下)"
                    print(f"  [完全保護] {v['kanji']} の通信エラーを検知。過去の全データを保護しました。")
                    # 【追加】除外フラグが立っていない「現役のガチャ対象駅」だけをエラーレポートに載せる
                    if not existing_stations[unique_key].get("is_excluded"):
                        error_stations.append(v['kanji'])
                else:
                    ignore_keys = {"id", "startDay", "endDay", "missingCount", "unique_key", "subPage", "url"}
                    #ignore_keys = {"id", "startDay", "endDay", "missingCount", "unique_key", "url"} #★ミスで廃駅にしてしまったとき用に、subPageを無視しない設定
                    station_changes = {}
                    for key in v.keys():
                        if key in ignore_keys:
                            continue
                        old_val = old_item.get(key)
                        new_val = v.get(key)
                        if old_val != new_val:
                            station_changes[key] = {"old": old_val, "new": new_val}

                    if station_changes:
                        updated_summary.append({
                            "kanji": v["kanji"],
                            "changes": station_changes
                        })
                        print(f"  [データ更新] {v['kanji']} の情報を更新（{len(station_changes)}項目）。")

                    existing_stations[unique_key] = v
                    existing_stations[unique_key]["id"] = preserved_id
                    existing_stations[unique_key]["startDay"] = preserved_start
                    existing_stations[unique_key]["endDay"] = preserved_end
                    existing_stations[unique_key]["missingCount"] = 0
            else:

                if v.get("is_sniper_skipped"):
                    continue

                # 詳細データの取得に失敗した新規駅はスキップする
                if v.get("min_km") is None and not v.get("address"):
                    print(f"  [スキップ] データ取得失敗のため新規登録を見送りました: {v['kanji']}")
                    error_stations.append(v['kanji'])
                    continue
                
                max_id += 1
                v["id"] = max_id
                v["startDay"] = 0
                v["endDay"] = 999999
                v["missingCount"] = 0
                existing_stations[unique_key] = v
                print(f"  [新駅検知] 新しい駅を追加 (ID: {max_id}): {v['kanji']}")

        # 3. 該当ページ内で消滅した駅の廃駅判定
        for unique_key, item in list(existing_stations.items()):
            if item.get("endDay", 999999) < 999999:
                continue

            sub_page = item.get("subPage")
            missing_count = item.get("missingCount", 0)

            # 【完全保護】今処理しているページ(page)以外の駅と、調査中の駅は判定から除外する
            if sub_page != page and sub_page != "引っ越し調査中":
                continue

            if sub_page == page:
                if unique_key not in page_fetched_keys:
                    # そのページから消えていた場合も引っ越し調査中にする
                    item["subPage"] = "引っ越し調査中"
                    item["missingCount"] = 1
                    print(f"  [消滅疑い] {item['kanji']} がリストに見当たりません。引っ越し調査中に移行します。")
                    continue

            if sub_page not in all_wikipedia_sub_pages and sub_page != "引っ越し調査中":
                item["subPage"] = "引っ越し調査中"
                item["missingCount"] = 1
                print(f"  [仕様変更検知] ページ消滅につき引っ越し調査中に設定: {item['kanji']}")
                continue

            if sub_page == "引っ越し調査中":
                if unique_key not in page_fetched_keys:
                    missing_count += 1
                    item["missingCount"] = missing_count
                    if missing_count >= 14:
                        item["endDay"] = current_day_index
                        item["subPage"] = "廃止済"
                        print(f"  [廃駅確定] 2週間未発見のため廃駅判定: {item['kanji']}")

        # 4. ID順にソートして即時保存
        result_list = list(existing_stations.values())
        result_list.sort(key=lambda x: x["id"])

        with open("stations.json", "w", encoding="utf-8") as f:
            json.dump(result_list, f, ensure_ascii=False, indent=2)

        print(f"\n💾 【途中保存完了】「{page}」ページの処理が終了し、'stations.json' に保存しました（現在合計: {len(result_list)} 駅）")

        time.sleep(2.0)

    # -------------------------------------------------------------------------
    # 全ページの完了・集計レポート出力
    # -------------------------------------------------------------------------
    length_counts = {}
    active_count = 0
    final_list = []
    if os.path.exists("stations.json"):
        with open("stations.json", "r", encoding="utf-8") as f:
            final_list = json.load(f)

    for s in final_list:
        if s.get("endDay", 999999) == 999999:
            active_count += 1
            l = len(s["yomi"])
            length_counts[l] = length_counts.get(l, 0) + 1

    print("\n========================================")
    print("全割り当て分の抽出・同期が完了しました！ 現役駅の文字数内訳：")
    for length in sorted(length_counts.keys()):
        print(f" {length:2}文字の駅名 : {length_counts[length]:4} 駅")
    print("----------------------------------------")
    print(f"👉 総データ数 : {len(final_list)} 駅 (うち現役: {active_count} 駅) が 'stations.json' に保存されています。")
    print("========================================")

    if updated_summary:
        print("\n========================================")
        print(f"🔄 【更新レポート】既存データに変更があった駅（計 {len(updated_summary)} 駅）")
        print("========================================")
        for info in updated_summary:
            print(f"■ {info['kanji']}")
            for k, diff in info['changes'].items():
                print(f"  - {k}: {diff['old']} -> {diff['new']}")
        print("========================================")

    if error_stations:
        print("\n========================================")
        print(f"⚠️ 【要リカバリー】データ取得に失敗した駅（計 {len(error_stations)} 駅）")
        print("以下のリストを TARGET_STATIONS にコピペして個別取得してください。")
        print(f"TARGET_STATIONS = {error_stations}")
        print("========================================")

    if url_completed_stations:
        print("\n========================================")
        print(f"✨ 【URL補完レポート】テキスト直書きからURLを自動補完した駅（計 {len(url_completed_stations)} 駅）")
        for st in url_completed_stations:
            print(f"  - {st}")
        print("========================================")

    if failed_wiki_links:
        print("\n========================================")
        print(f"⚠️ 【取得失敗レポート】路線や事業者の正式タイトル取得に最終失敗した項目（計 {len(failed_wiki_links)} 件）")
        print("※ 通信エラーまたはページが存在しないため、Wikipedia上のリンク名をそのまま採用しました。")
        for fail in failed_wiki_links:
            print(f"  - {fail}")
        print("========================================")

    # 【必須データ欠落チェック（バリデーション）】
    missing_data_alerts = []
    for s in final_list:
        # 現役かつ、除外対象（貨物・ケーブル等）ではない駅だけをチェックする
        if s.get("endDay", 999999) == 999999 and not s.get("is_excluded"):
            missing = []
            if not s.get("companies"): missing.append("事業者")
            if not s.get("lines"): missing.append("路線")
            if not s.get("yomi"): missing.append("よみ")
            if not s.get("latitude") or not s.get("longitude"): missing.append("座標")
            if not s.get("pref"): missing.append("都道府県")
            if not s.get("municipality"): missing.append("市区町村")
            if not s.get("population"): missing.append("人口(統計データ)")
            
            if missing:
                missing_data_alerts.append(f"  - {s['kanji']} : {', '.join(missing)} がありません")

    if missing_data_alerts:
        print("\n========================================")
        print(f"🚨 【要パッチ修正】必須データが欠落している現役駅（計 {len(missing_data_alerts)} 駅）")
        print("以下の駅はゲーム進行に支障が出るため、MANUAL_PATCH に手動でデータを追記してください。")
        for alert in missing_data_alerts:
            print(alert)
        print("========================================")

    # 適用した手動パッチ一覧の出力 
    if patched_stations:
        print("\n========================================")
        print(f"🔧 【パッチ適用レポート】手動補正パッチを適用した駅（計 {len(patched_stations)} 駅）")
        for p_name, p_items in patched_stations.items():
            print(f"  - {p_name} : 補正項目 ({', '.join(p_items)})")
        print("========================================")

    # 除外された駅の一覧レポート
    excluded_report = [s for s in final_list if s.get("endDay", 999999) == 999999 and s.get("is_excluded")]
    
    if excluded_report:
        print("\n========================================")
        print(f"🚫 【除外駅レポート】ガチャ対象外として処理された現役駅（計 {len(excluded_report)} 駅）")
        print("※もしこの中に「通常の旅客駅」が混ざっていた場合は、MANUAL_PATCH に 'is_excluded': False を設定して救済してください。")
        for ex in excluded_report:
            reason = ex.get("exclusion_reason", "不明")
            url = urllib.parse.unquote(ex.get("url", ""))
            print(f"  - [{reason}] {ex['kanji']} : {url}")
        print("========================================")


# Python発火スイッチ
if __name__ == "__main__":
    extract_and_count_stations()