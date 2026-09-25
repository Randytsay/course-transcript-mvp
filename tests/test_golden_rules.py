from __future__ import annotations

import unittest

from app.canonical.golden_rules import (
    GOLDEN_RULESET_VERSION,
    audit_golden_variants,
    golden_reference_instruction,
    golden_terms,
    merge_golden_terms,
)


class GoldenRulesTests(unittest.TestCase):
    def test_rules_are_bounded_human_reference(self) -> None:
        terms = golden_terms()
        self.assertGreaterEqual(len(terms), 15)
        self.assertTrue(all(item["source"] == GOLDEN_RULESET_VERSION for item in terms))
        by_canonical = {item["canonical"]: item for item in terms}
        self.assertIn("舍利佛", by_canonical["舍利弗"]["variants"])
        self.assertIn("下安居", by_canonical["夏安居"]["variants"])
        self.assertIn("三句功德", by_canonical["三聚功德"]["variants"])
        self.assertIn("阿摩羅國", by_canonical["菴摩勒果"]["variants"])
        self.assertNotIn("阿摩羅", by_canonical["菴摩勒果"]["variants"])
        self.assertIn("歸依", by_canonical["皈依"]["variants"])
        self.assertIn("四頭抹塵", by_canonical["翅頭末城"]["variants"])
        self.assertIn("阿摩勒國", by_canonical["菴摩勒果"]["variants"])
        self.assertIn("遊旬", by_canonical["由旬"]["variants"])
        self.assertIn("流璃", by_canonical["琉璃"]["variants"])
        self.assertIn("刺頭末", by_canonical["翅頭末"]["variants"])
        self.assertIn("是陀末", by_canonical["翅頭末"]["variants"])
        self.assertIn("車渠", by_canonical["硨磲"]["variants"])
        self.assertIn("念佛取淨", by_canonical["念佛取盡"]["variants"])
        self.assertIn("念佛取境", by_canonical["念佛取盡"]["variants"])
        self.assertIn("珍潔", by_canonical["增劫"]["variants"])

    def test_merge_preserves_provider_terms_and_golden_wins_same_term(self) -> None:
        merged = merge_golden_terms(
            [
                {"canonical": "自訂術語", "variants": ["自訂誤字"], "confidence": "medium"},
                {"canonical": "舍利弗", "variants": ["舍利佛"], "confidence": "low"},
            ]
        )
        by_canonical = {item["canonical"]: item for item in merged}
        self.assertIn("自訂術語", by_canonical)
        self.assertEqual(by_canonical["舍利弗"]["confidence"], "high")
        self.assertEqual(by_canonical["舍利弗"]["variants"].count("舍利佛"), 1)

    def test_audit_is_report_only_and_finds_known_variants(self) -> None:
        report = audit_golden_variants(
            [
                {"segment_id": "a", "corrected_text": "今天講下安居與舍利佛"},
                {"segment_id": "b", "corrected_text": "慈心三昧"},
            ]
        )
        self.assertFalse(report["timestamps_modified"])
        self.assertFalse(report["segments_modified"])
        found = {item["canonical"] for item in report["issues"]}
        self.assertIn("夏安居", found)
        self.assertIn("舍利弗", found)
        self.assertNotIn("慈心三昧", found)

    def test_fifth_lesson_gold_terms_are_auditable(self) -> None:
        report = audit_golden_variants(
            [
                {"segment_id": "a", "corrected_text": "如觀掌中阿摩羅國"},
                {"segment_id": "b", "corrected_text": "四頭抹塵所有一切大眾"},
                {"segment_id": "c", "corrected_text": "歸依未來大慈悲者"},
                {"segment_id": "d", "corrected_text": "無餘涅盤而滅度之"},
            ]
        )
        found = {item["canonical"] for item in report["issues"]}
        self.assertTrue({"菴摩勒果", "翅頭末城", "皈依", "涅槃"} <= found)

    def test_sixth_lesson_gold_terms_are_auditable(self) -> None:
        report = audit_golden_variants(
            [
                {"segment_id": "a", "corrected_text": "如觀掌中阿摩勒國"},
                {"segment_id": "b", "corrected_text": "彌勒佛國同於記命"},
                {"segment_id": "c", "corrected_text": "出會龍華的眾生"},
                {"segment_id": "d", "corrected_text": "三千遊旬"},
                {"segment_id": "e", "corrected_text": "如流璃鏡"},
            ]
        )
        found = {item["canonical"] for item in report["issues"]}
        self.assertTrue({"菴摩勒果", "淨命", "初會龍華", "由旬", "琉璃"} <= found)

    def test_seventh_lesson_gold_terms_are_auditable(self) -> None:
        report = audit_golden_variants(
            [
                {"segment_id": "a", "corrected_text": "刺頭末又稱淨義"},
                {"segment_id": "b", "corrected_text": "車渠瑪瑙"},
                {"segment_id": "c", "corrected_text": "大龍王名多羅斯棄"},
                {"segment_id": "d", "corrected_text": "名跋陀跋羅設宅家"},
                {"segment_id": "e", "corrected_text": "走在曲泉當中"},
                {"segment_id": "f", "corrected_text": "安樂淡泊念佛取淨"},
            ]
        )
        found = {item["canonical"] for item in report["issues"]}
        self.assertTrue(
            {"翅頭末", "淨意", "硨磲", "多羅尸棄", "跋陀婆羅賒塞迦", "渠泉", "念佛取盡"}
            <= found
        )

    def test_eighth_lesson_gold_terms_are_auditable(self) -> None:
        report = audit_golden_variants(
            [
                {"segment_id": "a", "corrected_text": "是陀末城"},
                {"segment_id": "b", "corrected_text": "名跋陀婆羅薩宅家"},
                {"segment_id": "c", "corrected_text": "間樹曲全"},
                {"segment_id": "d", "corrected_text": "安樂淡泊念佛取境"},
                {"segment_id": "e", "corrected_text": "從珍潔一直遞減"},
                {"segment_id": "f", "corrected_text": "從六式法到這裡"},
            ]
        )
        found = {item["canonical"] for item in report["issues"]}
        self.assertTrue(
            {"翅頭末", "跋陀婆羅賒塞迦", "渠泉", "念佛取盡", "增劫", "六事法"}
            <= found
        )

    def test_ninth_lesson_gold_terms_are_auditable(self) -> None:
        report = audit_golden_variants(
            [
                {"segment_id": "a", "corrected_text": "在音地修七支供養"},
                {"segment_id": "b", "corrected_text": "這叫八聖處，先講四聖處"},
                {"segment_id": "c", "corrected_text": "第四禪就是舍念清淨"},
                {"segment_id": "d", "corrected_text": "芬陀利花就是白蓮花"},
                {"segment_id": "e", "corrected_text": "美音鳩雕"},
                {"segment_id": "f", "corrected_text": "仙白七日香花終無畏時"},
            ]
        )
        found = {item["canonical"] for item in report["issues"]}
        self.assertTrue(
            {"因地", "八勝處", "四勝處", "捨念清淨", "分陀利華", "鳩鵰", "鮮白七日香華", "終無萎時"}
            <= found
        )

    def test_tenth_lesson_gold_terms_are_auditable(self) -> None:
        report = audit_golden_variants(
            [
                {"segment_id": "a", "corrected_text": "名曰朗居，祇樹祇孤獨園"},
                {"segment_id": "b", "corrected_text": "千輻輻輪皆悉具足，七之俱足，賢賢可觀"},
                {"segment_id": "c", "corrected_text": "三馬寶，諸劣馬尾"},
                {"segment_id": "d", "corrected_text": "一缽多藏在千陀羅國"},
                {"segment_id": "e", "corrected_text": "缽羅加藏在彌提羅國"},
                {"segment_id": "f", "corrected_text": "兵且羅藏在拘羅藏國"},
                {"segment_id": "g", "corrected_text": "由俱藏龍守護，其他才女沒有生育"},
                {"segment_id": "h", "corrected_text": "佛母到毗尼藍園"},
            ]
        )
        found = {item["canonical"] for item in report["issues"]}
        self.assertTrue(
            {
                "穰佉", "祇樹給孤獨園", "千輻轂輞", "七胑拄地", "嚴顯可觀",
                "紺馬寶", "朱鬣髦尾", "伊鉢多", "乾陀羅國", "般軸迦",
                "彌緹羅國", "賓伽羅", "須羅吒國", "伏藏龍", "婇女", "藍毘尼園"
            } <= found
        )

    def test_eleventh_lesson_gold_terms_are_auditable(self) -> None:
        report = audit_golden_variants(
            [
                {"segment_id": "a", "corrected_text": "佛母叫梵摩跋提"},
                {"segment_id": "b", "corrected_text": "證得無上妙圓四心三昧"},
                {"segment_id": "c", "corrected_text": "菩薩有隔音之迷"},
                {"segment_id": "d", "corrected_text": "佛說淨土三昧經"},
                {"segment_id": "e", "corrected_text": "第一個七日叫結羅蘭"},
                {"segment_id": "f", "corrected_text": "香音就是乾闥婆"},
                {"segment_id": "g", "corrected_text": "甩如鹿王相"},
                {"segment_id": "h", "corrected_text": "自阿多羅三藐三菩提"},
            ]
        )
        found = {item["canonical"] for item in report["issues"]}
        self.assertTrue(
            {
                "梵摩拔提", "慈心三昧", "隔陰之迷", "淨度三昧經",
                "羯羅藍", "香陰", "腨如鹿王相", "阿耨多羅三藐三菩提"
            } <= found
        )

    def test_twelfth_lesson_gold_terms_are_auditable(self) -> None:
        report = audit_golden_variants(
            [
                {"segment_id": "a", "corrected_text": "梵音千眼相"},
                {"segment_id": "b", "corrected_text": "三衣是安安陀會"},
                {"segment_id": "c", "corrected_text": "還有鬱多鬱多羅僧"},
                {"segment_id": "d", "corrected_text": "阿彌陀佛60億那由他恆河沙的身量"},
            ]
        )
        found = {item["canonical"] for item in report["issues"]}
        self.assertTrue(
            {"梵音聲遠相", "安陀會", "鬱多羅僧", "六十萬億那由他恆河沙"}
            <= found
        )

    def test_thirteenth_lesson_gold_terms_are_auditable(self) -> None:
        report = audit_golden_variants(
            [
                {"segment_id": "a", "corrected_text": "朗俱王與大臣"},
                {"segment_id": "b", "corrected_text": "厭家破哲如牢獄"},
                {"segment_id": "c", "corrected_text": "還有五陰自盛"},
                {"segment_id": "d", "corrected_text": "建立平等清淨床相"},
                {"segment_id": "e", "corrected_text": "叫做法幹琉璃相"},
                {"segment_id": "f", "corrected_text": "成就後豁然無所礙"},
            ]
        )
        found = {item["canonical"] for item in report["issues"]}
        self.assertTrue(
            {"穰佉", "迫迮", "五陰熾盛", "清淨幢", "髮紺琉璃相", "霍然無所礙"}
            <= found
        )

    def test_fourteenth_lesson_gold_terms_are_auditable(self) -> None:
        report = audit_golden_variants(
            [
                {"segment_id": "a", "corrected_text": "迦葉尊者他是闢支佛"},
                {"segment_id": "b", "corrected_text": "諸佛所轉八乘道輪"},
                {"segment_id": "c", "corrected_text": "如來所說浴法雨"},
                {"segment_id": "d", "corrected_text": "使於今日法海滿"},
                {"segment_id": "e", "corrected_text": "再來就是五分香"},
            ]
        )
        found = {item["canonical"] for item in report["issues"]}
        self.assertTrue(
            {"辟支佛", "八聖道", "雨法雨", "始於今日法海滿", "五分法身香"}
            <= found
        )

    def test_fifteenth_lesson_gold_terms_are_auditable(self) -> None:
        report = audit_golden_variants(
            [
                {"segment_id": "a", "corrected_text": "印度叫做團食"},
                {"segment_id": "b", "corrected_text": "一段一段吃這叫斷食"},
                {"segment_id": "c", "corrected_text": "受細滑處"},
                {"segment_id": "d", "corrected_text": "如來放光摩莎灌頂"},
                {"segment_id": "e", "corrected_text": "比丘並法之落"},
            ]
        )
        found = {item["canonical"] for item in report["issues"]}
        self.assertTrue(
            {"摶食", "段食", "細滑觸", "摩頂受記", "鬚髮自落"}
            <= found
        )

    def test_sixteenth_lesson_gold_terms_are_auditable(self) -> None:
        report = audit_golden_variants(
            [
                {"segment_id": "a", "corrected_text": "入翅頭末城足躡門檻"},
                {"segment_id": "b", "corrected_text": "上至阿加尼吒天"},
                {"segment_id": "c", "corrected_text": "東方阿楚如來、阿楚佛"},
                {"segment_id": "d", "corrected_text": "前五識轉為成所作識"},
                {"segment_id": "e", "corrected_text": "三果聖人的色就竟天"},
                {"segment_id": "f", "corrected_text": "他有具身我執、具身法執、具身無明"},
            ]
        )
        found = {item["canonical"] for item in report["issues"]}
        self.assertTrue(
            {
                "足躡門閫", "阿迦膩吒天", "阿閦佛", "阿閦如來",
                "成所作智", "色究竟天", "俱生我執", "俱生法執", "俱生無明"
            } <= found
        )

    def test_seventeenth_lesson_gold_terms_are_auditable(self) -> None:
        report = audit_golden_variants(
            [
                {"segment_id": "a", "corrected_text": "那畢舍遮是一種惡鬼"},
                {"segment_id": "b", "corrected_text": "又叫啖精氣鬼"},
                {"segment_id": "c", "corrected_text": "他領的叫做鳩盤荼"},
                {"segment_id": "d", "corrected_text": "又稱厭魅鬼"},
                {"segment_id": "e", "corrected_text": "摩睺羅迦就是鬼眾"},
            ]
        )
        found = {item["canonical"] for item in report["issues"]}
        self.assertTrue(
            {"毘舍遮", "噉精氣鬼", "鳩槃荼", "魘魅鬼", "摩睺羅伽"} <= found
        )

    def test_eighteenth_lesson_gold_terms_are_auditable(self) -> None:
        report = audit_golden_variants(
            [
                {"segment_id": "a", "corrected_text": "復有離須達多、俱藍那兄弟"},
                {"segment_id": "b", "corrected_text": "俱留那尊者說法第一"},
                {"segment_id": "c", "corrected_text": "一名梵摩尼"},
                {"segment_id": "d", "corrected_text": "轉輪王寶女名奢彌婆睇"},
                {"segment_id": "e", "corrected_text": "今毗舍佉母是"},
                {"segment_id": "f", "corrected_text": "無奈何者，教植來緣"},
                {"segment_id": "g", "corrected_text": "教與他人，使得受持"},
            ]
        )
        found = {item["canonical"] for item in report["issues"]}
        self.assertTrue(
            {
                "梨師達多", "富蘭那", "富樓那", "梵檀末利", "舍彌婆帝",
                "毘舍佉", "教殖來緣", "無奈汝何", "教於他人", "令得受持"
            } <= found
        )

    def test_nineteenth_lesson_gold_terms_are_auditable(self) -> None:
        report = audit_golden_variants(
            [
                {"segment_id": "a", "corrected_text": "再來講毗尼跟阿毗曇"},
                {"segment_id": "b", "corrected_text": "週利盤陀伽的大哥叫摩呵盤陀伽"},
                {"segment_id": "c", "corrected_text": "嫉度之後就嫉恩吝、嫉貪"},
                {"segment_id": "d", "corrected_text": "法名如來就是俱留他的尊者"},
                {"segment_id": "e", "corrected_text": "未來不受飢謹之災"},
                {"segment_id": "f", "corrected_text": "如果我們有法員"},
                {"segment_id": "g", "corrected_text": "毗婆屍佛、屍棄佛、毗舍浮佛"},
            ]
        )
        found = {item["canonical"] for item in report["issues"]}
        self.assertTrue(
            {
                "毘尼", "阿毘曇", "周利槃陀伽", "摩訶槃陀伽",
                "慳", "慳貪", "嫉妒", "法明如來", "富樓那",
                "饑饉", "法緣", "毘婆尸佛", "尸棄佛", "毘舍浮佛"
            } <= found
        )

    def test_twentieth_lesson_gold_terms_are_auditable(self) -> None:
        report = audit_golden_variants(
            [
                {"segment_id": "a", "corrected_text": "是否末乘來集會的大眾"},
                {"segment_id": "b", "corrected_text": "稽首皈依俱嚕悉地"},
            ]
        )
        found = {item["canonical"] for item in report["issues"]}
        self.assertTrue({"翅頭末城", "蘇悉帝"} <= found)

    def test_audit_counts_one_variant_per_term_per_segment(self) -> None:
        report = audit_golden_variants(
            [{"segment_id": "a", "corrected_text": "如觀掌中阿摩羅國"}]
        )
        issue = next(item for item in report["issues"] if item["canonical"] == "菴摩勒果")
        self.assertEqual(issue["hits"], [{"segment_id": "a", "variant": "阿摩羅國"}])

    def test_prompt_contract_is_reference_not_blind_replacement(self) -> None:
        prompt = golden_reference_instruction()
        self.assertIn("do not force a replacement", prompt)
        self.assertIn("segment IDs or timestamps", prompt)
        self.assertIn("舍利佛 → 舍利弗", prompt)


if __name__ == "__main__":
    unittest.main()
