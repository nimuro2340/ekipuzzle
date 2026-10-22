#軽量辞書を更に軽量化し、フロントエンドにアップロードする辞書を作るプログラム

import json
import datetime 

def create_frontend_dict():
    # 基準日（2024年1月1日）からの経過日数を計算
    base_date = datetime.datetime(2024, 1, 1)
    today_dt = datetime.datetime.today()
    current_day_index = (today_dt - base_date).days

    # 元となる軽量辞書を読み込む
    with open("stations_lite.json", "r", encoding="utf-8") as f:
        lite_dict = json.load(f)

    frontend_dict = {}

    for uk, data in lite_dict.items():
        # フロントエンド辞書への出力除外フィルタ
        
        # 1. 貨物以外の除外駅（is_excluded: True）をスキップ
        if data.get("is_excluded", False):
            continue
            
        # 2. 廃止から33日以上経過している駅をスキップ
        end_day = data.get("endDay", 999999)
        if end_day != 999999 and end_day <= current_day_index - 33:
            continue

    for uk, data in lite_dict.items():
        # ゲーム本編の画面表示や距離計算で絶対に使うデータだけを抽出する
        # 管理用のフラグ（is_active, is_excludedなど）はすべて切り捨てる
        frontend_data = {
            "kanji": data.get("kanji", ""),         # 表記
            "yomi": data.get("yomi", ""),           # 読み
            "latitude": data.get("latitude"),       # 緯度
            "longitude": data.get("longitude"),     # 経度
            "pref": data.get("pref", ""),           # 都道府県
            "county": data.get("county", ""),       #  郡
            "municipality": data.get("municipality", ""),   # 市区町村
            "ward": data.get("ward", ""),           #  行政区
            "companies": data.get("companies", []), # 所属事業者
            "lines": data.get("lines", []),         # 所属路線
            "url": data.get("url", "")              # WikipediaのURL
        }

        # 開始日と終了日（現役はフラグ自体省略）
        if "startDay" in data:
            frontend_data["startDay"] = data["startDay"]
        if "endDay" in data:
            frontend_data["endDay"] = data["endDay"]
        
        frontend_dict[uk] = frontend_data

    # 超軽量辞書として新しいファイルに書き出す
    with open("stations_frontend.json", "w", encoding="utf-8") as f:
        # separatorsを指定して改行や無駄なスペースを完全に排除し、極限までファイル容量を削る
        json.dump(frontend_dict, f, ensure_ascii=False, separators=(',', ':'))

    print("✅ 超軽量フロントエンド辞書（stations_frontend.json）を作成しました！")
    print("このファイルをCloudflare Pages等にアップロードしてゲームから読み込ませてください。")

if __name__ == "__main__":
    create_frontend_dict()