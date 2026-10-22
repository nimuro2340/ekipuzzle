# 回答作成に使う軽量辞書を作るプログラム（アップロード用辞書ではない）

import json

def create_lite_dictionary():
    
    with open("stations.json", "r", encoding="utf-8") as f:
        stations = json.load(f)

    lite_dict = {}

    for s in stations:
        uk = s.get("unique_key")
        if not uk:
            continue
            
        # 廃駅かどうかを endDay から判定して boolean（True/False）化
        is_active = (s.get("endDay", 999999) == 999999)

        # Cloudflareでフロントエンド・バックエンドの処理に必要な項目を網羅
        # 回答作成用の軽量辞書に登録する項目
        lite_dict[uk] = {
            "kanji": s.get("kanji", ""),
            "yomi": s.get("yomi", ""),
            "pref": s.get("pref", ""),
            "county": s.get("county", ""),
            "municipality": s.get("municipality", ""),
            "ward": s.get("ward", ""),
            "latitude": s.get("latitude"),
            "longitude": s.get("longitude"),
            "companies": s.get("companies", []),
            "lines": s.get("lines", []),
            "url": s.get("url", ""),
            "is_excluded": s.get("is_excluded", False),
            "is_active": is_active  # 回答作成用現役フラグ（入力は廃止後暫く入力可能だが、答えは廃止後すぐに排除する関係で別）
        }

        # StartDayやEndDayが現役とは異なる場合は付与、現役は省略
        start_day = s.get("startDay", 0)
        end_day = s.get("endDay", 999999)
        if start_day != 0:
            lite_dict[uk]["startDay"] = start_day
        if end_day != 999999:
            lite_dict[uk]["endDay"] = end_day

    # separators=(',', ':') で構造の余白だけを圧縮（文字列の空白は安全）
    with open("stations_lite.json", "w", encoding="utf-8") as f:
        json.dump(lite_dict, f, ensure_ascii=False, separators=(',', ':'))

    print(f"✅ 軽量辞書の作成完了: {len(lite_dict)}駅のデータを stations_lite.json に保存しました。")
    
    # 圧縮の確認用サンプル出力
    sample_key = list(lite_dict.keys())[0]
    print(f"\n【参考】辞書のデータ構造例（1件）:")
    print(json.dumps({sample_key: lite_dict[sample_key]}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    create_lite_dictionary()