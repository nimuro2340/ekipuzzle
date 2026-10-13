import json

def check_stations_data():
    # 辞書データの読み込み
    try:
        with open("stations.json", "r", encoding="utf-8") as f:
            stations = json.load(f)
    except FileNotFoundError:
        print("エラー: stations.json が見つかりません。")
        return

    missing_data_stations = []
    excluded_stations = []
    freight_only_stations = []

    for s in stations:
        kanji = s.get("kanji", "")
        yomi = s.get("yomi", "")
        uk = s.get("unique_key", kanji)
        display_name = kanji if kanji else (uk if uk else "名前不明の駅")

        # 1. 除外対象・廃駅の判定
        is_excluded = s.get("is_excluded", False)
        is_abolished = s.get("is_abolished_confirmed", False)
        # 終了日が設定されている（過去の日付）かも簡易チェック
        is_past_end_day = s.get("endDay", 999999) < 20000  # ざっくりとした過去日判定
        
        if is_excluded or is_abolished or is_past_end_day:
            reason = s.get("exclusion_reason", "")
            if not reason:
                reason = "廃駅" if is_abolished else "除外設定"
            excluded_stations.append(f"{display_name} ({reason})")
            # 廃駅や除外駅は、必須データがなくてもゲームに出ないのでチェックをスキップ
            continue 

        # 2. 必須データの欠損判定（現役の駅のみを対象）
        missing_fields = []
        
        if not kanji: missing_fields.append("kanji(表記)")
        if not yomi: missing_fields.append("yomi(読み)")
        if s.get("latitude") is None: missing_fields.append("latitude(緯度)")
        if s.get("longitude") is None: missing_fields.append("longitude(経度)")
        if not s.get("pref"): missing_fields.append("pref(都道府県)")
        if not s.get("municipality"): missing_fields.append("municipality(市区町村)")
        
        # 事業者のチェックと貨物専用駅のあぶり出し
        companies = s.get("companies")
        if not companies or len(companies) == 0:
            missing_fields.append("companies(事業者)")
        elif len(companies) == 1 and companies[0] == "日本貨物鉄道":
            freight_only_stations.append(display_name)
            continue # 貨物駅も出題されないので欠損チェックを終了

        # 路線のチェック
        lines = s.get("lines")
        if not lines or len(lines) == 0:
            missing_fields.append("lines(路線)")

        # もし一つでも欠損があればリストに追加
        if missing_fields:
            missing_data_stations.append({
                "name": display_name,
                "missing": missing_fields
            })

    # ==========================================
    # 診断結果の出力
    # ==========================================
    print(f"========== 辞書データ診断結果 ==========")
    print(f"総駅数: {len(stations)} 駅")
    print(f"除外/廃駅設定されている駅: {len(excluded_stations)} 駅")
    print(f"貨物専用駅: {len(freight_only_stations)} 駅")
    print(f"必須データが欠損している現役駅: {len(missing_data_stations)} 駅")
    print(f"========================================\n")

    if missing_data_stations:
        print("🚨 【データ欠損駅一覧】（修正が必要です）")
        for item in missing_data_stations:
            print(f"・{item['name']} ➔ 欠損: {', '.join(item['missing'])}")
    else:
        print("✨ 素晴らしい！現役の駅に必須データの欠損はありませんでした。")

if __name__ == '__main__':
    check_stations_data()