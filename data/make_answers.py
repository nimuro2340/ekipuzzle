# 軽量化辞書から今後10年分の回答を作成するためのプログラム

import json
import random
from datetime import datetime, timedelta
import os

# ==========================================
# 【ゲーム設定】モード増減時の自動計算用パラメータ
# ==========================================
# ガチャがエラーで停止しないよう、各文字数の「1日あたりの消費枠数」を定義する。
# モードを増やした時は、ここの数式に足し算するだけで全自動で安全日数が再計算される。
BURN_RATES = {
    "loca": 2.0,                   # 駅ロケ: ノーマル(1) + ハード(1)
    "eki_4": 1.0 + (4.0/7.0),      # 駅ドル4文字: チャレ(1) + クアッド週4枠(4/7)
    "eki_5": 1.0 + 1.0 + (4.0/7.0),# 駅ドル5文字: デイリー(1) + チャレ(1) + クアッド週4枠(4/7)
    "eki_6": 1.0 + (4.0/7.0)       # 駅ドル6文字: チャレ(1) + クアッド週4枠(4/7)
}
# ==========================================

class StationDeck:
    def __init__(self, items):
        self.initial_total = len(items)
        self.items = items.copy()
        random.shuffle(self.items)
        self.used = []
        
    def draw(self, condition_func):
        # ストックが20%以下になったら、使用済みの駅を山札に戻して再度シャッフルする
        if len(self.items) <= max(1, self.initial_total * 0.2):
            self.items.extend(self.used)
            random.shuffle(self.items)
            self.used = []
            
        # 山札の上から順番に条件に合う駅を探す
        for i, uk in enumerate(self.items):
            if condition_func(uk):
                self.items.pop(i)
                self.used.append(uk)
                return uk
        
        # 条件を満たす駅が一つもない場合の緊急措置として先頭の駅を引く
        if self.items:
            uk = self.items.pop(0)
            self.used.append(uk)
            return uk
        return None

def create_schedule():
    # 辞書データから全駅の情報を読み込む
    with open("stations_lite.json", "r", encoding="utf-8") as f:
        lite_dict = json.load(f)

    # 各ゲーム用の空のリストを準備
    all_4, all_5, all_6, all_loca, all_tori = [], [], [], [], []
    
    # 現役かつ除外フラグがない駅だけを抽出し、文字数や用途ごとに分類する
    for uk, data in lite_dict.items():
        if data.get("is_active") and not data.get("is_excluded"):
            kanji = data.get("kanji")
            yomi = data.get("yomi", "")
            url = data.get("url")
            pref = data.get("pref")
            muni = data.get("municipality")
            lat = data.get("latitude")
            lon = data.get("longitude")
            companies = data.get("companies", [])
            lines = data.get("lines", [])
            # 貨物駅判定（事業者の全ての要素が貨物駅である）
            is_freight_only = len(companies) > 0 and all(c in ["日本貨物鉄道", "JR貨物"] for c in companies) 

            # 【全ゲーム共通のフィルター】
            # 表記・読み・URL が全て存在し、かつ貨物駅ではない「健全な駅」だけを抽出
            is_healthy_station = bool(kanji and yomi and url) and not is_freight_only
            
            if is_healthy_station:
                
                # --- 駅トリ用 ---
                # 健全な駅であれば、文字数関係なく全てしりとりの出題対象にする
                all_tori.append(uk)
                
                # --- 駅ドル用 ---
                # 健全な駅のうち、文字数ごとに振り分ける
                y_len = len(yomi)
                if y_len == 4: all_4.append(uk)
                elif y_len == 5: all_5.append(uk)
                elif y_len == 6: all_6.append(uk)
                
                # --- 駅ロケ用 ---
                # 健全な駅のうち、さらに座標・都道府県・市区町村・事業者・路線の詳細データが完備されている駅
                if lat is not None and lon is not None and pref and muni and companies and lines:
                    all_loca.append(uk)
                
    # 各カテゴリの山札を作成する
    deck_4 = StationDeck(all_4)
    deck_5 = StationDeck(all_5)
    deck_6 = StationDeck(all_6)
    deck_loca = StationDeck(all_loca)
    deck_tori = StationDeck(all_tori) # ★ 駅トリ用の山札もここで完成！

    print(f"🎯 初期ストック: ロケ用 {len(all_loca)}駅 | トリ用 {len(all_tori)}駅 | 4文字 {len(all_4)}駅 | 5文字 {len(all_5)}駅 | 6文字 {len(all_6)}駅")

    schedule = {}
    # すでにスケジュールが存在する場合は読み込む
    if os.path.exists("secret_schedule.json"):
        with open("secret_schedule.json", "r", encoding="utf-8") as f:
            schedule = json.load(f)
            
    today_dt = datetime.today()
    # 今日から14日後までの期間を計算し、既存データを保護する境界線とする
    protection_end_dt = today_dt + timedelta(days=14)
    protection_end_date = protection_end_dt.strftime("%Y-%m-%d")
    final_schedule = {}
    
    yomi_history = {} 
    coord_history = {} 
    current_quad_4 = {} 
    current_quad_5 = {} 
    current_quad_6 = {}
    
    start_date = datetime.strptime(today_dt.strftime("%Y-%m-%d"), "%Y-%m-%d")
    
    # 保護期間内の既存スケジュールをそのまま引き継ぎ、過去の出題履歴を記憶させる
    for date_str in sorted(schedule.keys()):
        if date_str < protection_end_date:
            daily_data = schedule[date_str]
            final_schedule[date_str] = daily_data
            
            day_idx = (datetime.strptime(date_str, "%Y-%m-%d") - start_date).days
            
            # デイリーとチャレンジの読み込み
            for key in ["daily_5", "chal_4", "chal_5", "chal_6"]:
                uk = daily_data.get(key)
                if uk and uk in lite_dict:
                    yomi = lite_dict[uk].get("yomi")
                    if yomi: yomi_history[yomi] = day_idx
                    
            # 3種類のクアッドの読み込み
            for q_len in [4, 5, 6]:
                for q_idx in range(1, 5):
                    key = f"quad_{q_len}_{q_idx}"
                    uk = daily_data.get(key)
                    if uk and uk in lite_dict:
                        yomi = lite_dict[uk].get("yomi")
                        if yomi: yomi_history[yomi] = day_idx
                        if q_len == 4: current_quad_4[key] = uk
                        elif q_len == 5: current_quad_5[key] = uk
                        elif q_len == 6: current_quad_6[key] = uk
                        
            # 駅ロケの読み込み
            for key in ["loca_n", "loca_h"]:
                uk = daily_data.get(key)
                if uk and uk in lite_dict:
                    lat, lon = lite_dict[uk].get("latitude"), lite_dict[uk].get("longitude")
                    if lat is not None and lon is not None:
                        coord_history[(lat, lon)] = day_idx

    print(f"🛡️ 保護されたスケジュール: {len(final_schedule)}日分（{protection_end_date} 以前は変更されません）")
    print("⏳ 未来10年分（10枠）のスケジュールを完璧な被り回避で生成中...")

    # 保護期間明けから10年先までのスケジュールを生成する
    for i in range(3650):
        target_dt = start_date + timedelta(days=i)
        target_date = target_dt.strftime("%Y-%m-%d")
        
        # すでに保護されている日付はスキップしつつ、クアッドの状態だけは更新しておく
        if target_date in final_schedule:
            for q_idx in range(1, 5):
                q4_key = f"quad_4_{q_idx}"
                if q4_key in final_schedule[target_date]:
                    current_quad_4[q4_key] = final_schedule[target_date][q4_key]
                    
                q5_key = f"quad_5_{q_idx}"
                if q5_key in final_schedule[target_date]:
                    current_quad_5[q5_key] = final_schedule[target_date][q5_key]
                    
                q6_key = f"quad_6_{q_idx}"
                if q6_key in final_schedule[target_date]:
                    current_quad_6[q6_key] = final_schedule[target_date][q6_key]
            continue
            
        today_used_uk = set()
        today_used_yomi = set()
        today_used_coord = set()

        # 設定パネルの消費枠数を参照し、駅ロケのクーリング期間を動的に計算する
        cooling_loca = min(1000, int((deck_loca.initial_total * 0.7) / BURN_RATES["loca"]))

        def loca_condition(uk):
            # 同日内の重複を防ぐ
            if uk in today_used_uk: return False
            lat, lon = lite_dict[uk].get("latitude"), lite_dict[uk].get("longitude")
            if lat is not None and lon is not None:
                # 今日すでにノーマルで引かれた座標と同じならハードでは弾く
                if (lat, lon) in today_used_coord:
                    return False
                last_used = coord_history.get((lat, lon))
                # 計算されたクーリング期間以内に同じ座標が出ていたら弾く
                if last_used is not None and (i - last_used) < cooling_loca:
                    return False
            return True

        loca_n_uk = deck_loca.draw(loca_condition)
        if loca_n_uk: 
            today_used_uk.add(loca_n_uk)
            # 引いた駅の座標を今日の空箱に入れる
            lat, lon = lite_dict[loca_n_uk].get("latitude"), lite_dict[loca_n_uk].get("longitude")
            if lat is not None and lon is not None:
                today_used_coord.add((lat, lon))
        
        loca_h_uk = deck_loca.draw(loca_condition)
        if loca_h_uk: 
            today_used_uk.add(loca_h_uk)
            # ハードで引いた駅の座標も今日の空箱に入れる
            lat, lon = lite_dict[loca_h_uk].get("latitude"), lite_dict[loca_h_uk].get("longitude")
            if lat is not None and lon is not None:
                today_used_coord.add((lat, lon))

        # 抽出した駅の座標を履歴に登録する
        for uk in [loca_n_uk, loca_h_uk]:
            if uk and uk in lite_dict:
                lat, lon = lite_dict[uk].get("latitude"), lite_dict[uk].get("longitude")
                if lat is not None and lon is not None:
                    coord_history[(lat, lon)] = i

        # 5文字モードのクーリング期間を動的に計算する
        cooling_5 = min(1000, int((deck_5.initial_total * 0.7) / BURN_RATES["eki_5"]))

        def eki_condition_5(uk):
            if uk in today_used_uk: return False
            yomi = lite_dict[uk].get("yomi")
            if yomi in today_used_yomi: return False
            last_used = yomi_history.get(yomi)
            if last_used is not None and (i - last_used) < cooling_5:
                return False
            return True

        # 4文字、6文字モードのクーリング期間をそれぞれ動的に計算する
        cooling_4 = min(1000, int((deck_4.initial_total * 0.7) / BURN_RATES["eki_4"]))
        def eki_condition_4(uk):
            if uk in today_used_uk: return False
            yomi = lite_dict[uk].get("yomi")
            if yomi in today_used_yomi: return False
            last_used = yomi_history.get(yomi)
            if last_used is not None and (i - last_used) < cooling_4: return False
            return True
        
        cooling_6 = min(1000, int((deck_6.initial_total * 0.7) / BURN_RATES["eki_6"]))
        def eki_condition_6(uk):
            if uk in today_used_uk: return False
            yomi = lite_dict[uk].get("yomi")
            if yomi in today_used_yomi: return False
            last_used = yomi_history.get(yomi)
            if last_used is not None and (i - last_used) < cooling_6: return False
            return True

        is_monday = target_dt.weekday() == 0
        
        # 月曜日、またはクアッドの初期状態のときのみ新しく4駅×3モードを抽出する
        if is_monday or not current_quad_5:
            # 4文字クアッドの抽出
            q_uks_4 = {}
            for q_idx in range(1, 5):
                q_uk = deck_4.draw(eki_condition_4)
                if q_uk:
                    today_used_uk.add(q_uk)
                    yomi = lite_dict[q_uk].get("yomi")
                    if yomi:
                        today_used_yomi.add(yomi)
                        yomi_history[yomi] = i
                    q_uks_4[f"quad_4_{q_idx}"] = q_uk
            current_quad_4 = q_uks_4

            # 5文字クアッドの抽出
            q_uks_5 = {}
            for q_idx in range(1, 5):
                q_uk = deck_5.draw(eki_condition_5)
                if q_uk:
                    today_used_uk.add(q_uk)
                    yomi = lite_dict[q_uk].get("yomi")
                    if yomi:
                        today_used_yomi.add(yomi)
                        yomi_history[yomi] = i
                    q_uks_5[f"quad_5_{q_idx}"] = q_uk
            current_quad_5 = q_uks_5

            # 6文字クアッドの抽出
            q_uks_6 = {}
            for q_idx in range(1, 5):
                q_uk = deck_6.draw(eki_condition_6)
                if q_uk:
                    today_used_uk.add(q_uk)
                    yomi = lite_dict[q_uk].get("yomi")
                    if yomi:
                        today_used_yomi.add(yomi)
                        yomi_history[yomi] = i
                    q_uks_6[f"quad_6_{q_idx}"] = q_uk
            current_quad_6 = q_uks_6

        else:
            # 月曜以外は当日の被り防止用セットにだけ登録し、新しく抽出はしない
            for q_dict in [current_quad_4, current_quad_5, current_quad_6]:
                for q_uk in q_dict.values():
                    if q_uk:
                        today_used_uk.add(q_uk)
                        yomi = lite_dict[q_uk].get("yomi")
                        if yomi: today_used_yomi.add(yomi)

        # デイリー（5文字）の駅を抽出する
        daily_5_uk = deck_5.draw(eki_condition_5)
        if daily_5_uk:
            today_used_uk.add(daily_5_uk)
            yomi = lite_dict[daily_5_uk].get("yomi")
            if yomi:
                today_used_yomi.add(yomi)
                yomi_history[yomi] = i

        

        # チャレンジ（4, 5, 6文字）の駅を抽出する
        chal_4_uk = deck_4.draw(eki_condition_4)
        if chal_4_uk:
            today_used_uk.add(chal_4_uk)
            yomi = lite_dict[chal_4_uk].get("yomi")
            if yomi:
                today_used_yomi.add(yomi)
                yomi_history[yomi] = i

        chal_5_uk = deck_5.draw(eki_condition_5)
        if chal_5_uk:
            today_used_uk.add(chal_5_uk)
            yomi = lite_dict[chal_5_uk].get("yomi")
            if yomi:
                today_used_yomi.add(yomi)
                yomi_history[yomi] = i

        chal_6_uk = deck_6.draw(eki_condition_6)
        if chal_6_uk:
            today_used_uk.add(chal_6_uk)
            yomi = lite_dict[chal_6_uk].get("yomi")
            if yomi:
                today_used_yomi.add(yomi)
                yomi_history[yomi] = i

        # 1日分のスケジュールを確定し辞書に格納する
        final_schedule[target_date] = {
            "loca_n": loca_n_uk,
            "loca_h": loca_h_uk,
            "daily_5": daily_5_uk,
            "chal_4": chal_4_uk,
            "chal_5": chal_5_uk,
            "chal_6": chal_6_uk,
            "quad_4_1": current_quad_4.get("quad_4_1"),
            "quad_4_2": current_quad_4.get("quad_4_2"),
            "quad_4_3": current_quad_4.get("quad_4_3"),
            "quad_4_4": current_quad_4.get("quad_4_4"),
            "quad_5_1": current_quad_5.get("quad_5_1"),
            "quad_5_2": current_quad_5.get("quad_5_2"),
            "quad_5_3": current_quad_5.get("quad_5_3"),
            "quad_5_4": current_quad_5.get("quad_5_4"),
            "quad_6_1": current_quad_6.get("quad_6_1"),
            "quad_6_2": current_quad_6.get("quad_6_2"),
            "quad_6_3": current_quad_6.get("quad_6_3"),
            "quad_6_4": current_quad_6.get("quad_6_4")
        }

    # 完成したカレンダーをJSONファイルとして保存する
    with open("secret_answers.json", "w", encoding="utf-8") as f:
        json.dump(final_schedule, f, ensure_ascii=False, indent=2)

    print(f"✅ 全モード連動・10年分のカレンダー完成（全 {len(final_schedule)} 日分）！")

if __name__ == "__main__":
    create_schedule()