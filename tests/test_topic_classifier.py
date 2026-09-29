from app.jobs.content_context import DACHENG_BUDDHIST, GENERIC, HVAC_ENERGY, MARKET_AMERICA_TRAINING, normalize_content_mode
from app.jobs.topic_classifier import classify_topic

def test_market_america_training_routes_without_buddhist_leakage():
    r=classify_topic(folder_name="美安產品訓練 2026", source_name="Isotonix_OPC-3_NAD+.mp3", reference_sample="今天介紹 Market America、SHOP.COM、Isotonix OPC-3 與 IBV。")
    assert r["profile"] == MARKET_AMERICA_TRAINING
    assert r["scores"][MARKET_AMERICA_TRAINING] > r["scores"][DACHENG_BUDDHIST]

def test_hvac_energy_routes_from_engineering_terms():
    r=classify_topic(source_name="EMS_BESS_MV_training.mp3", document_context="IPMVP M&V、ASHRAE、ISO 50001 與 Modbus 技術訓練", chirp_sample="冰水主機與儲能系統需納入 EMS 及需量管理。")
    assert r["profile"] == HVAC_ENERGY

def test_dacheng_routes_only_with_strong_buddhist_evidence():
    r=classify_topic(folder_name="佛說彌勒大成佛經", reference_sample="今天繼續龍華三會，最後恭念得見彌勒根本大明神咒。")
    assert r["profile"] == DACHENG_BUDDHIST

def test_weak_content_falls_back_to_generic():
    r=classify_topic(source_name="週日課程.mp3", chirp_sample="今天我們來談一些實際案例與工作上的經驗。")
    assert r["profile"] == GENERIC

def test_conflicting_specialised_evidence_fails_safe():
    r=classify_topic(folder_name="佛說彌勒大成佛經 美安訓練", reference_sample="Market America SHOP.COM Isotonix；龍華三會 得見彌勒根本大明神咒。")
    assert r["profile"] == GENERIC
    assert r["requires_review"] is True

def test_new_content_modes():
    assert normalize_content_mode("GENERIC") == GENERIC
    assert normalize_content_mode("market_america_training") == MARKET_AMERICA_TRAINING
    assert normalize_content_mode("HVAC_ENERGY") == HVAC_ENERGY
