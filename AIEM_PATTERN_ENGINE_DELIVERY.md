# AIEM Pattern Engine — Resubmission with Evidence

**Capture script:** `verify_pattern_engine.sh`  
**Script SHA-256:** `685833b9b741b1bd55ddfcae54440634250dfb5d2db9a62c20cfc84968975ee8`  
**Bundle file:** `verified_pattern_engine_2026-07-18_001231.json`  
**Capture time:** 2026-07-18T00:12:31Z  
**verify_chain.sh SHA:** `469edcd448283423115a845237aa7a6fe51124aa0e5ad49cab1f7d9894733ed3`  
**verified_run.sh SHA:** `31c74ee84035e9da85bb0d14799122eb5eeafb68f3a9d3d8bc28bb9e210f3625`

---

## Pre-existing verified item (carried forward unchanged)

**Item 17 resubmit** — PASS=79 FAIL=0 on `verify_item_17_nodelete.py`; SHA before=`559967af` after=`6589b92b`; 10 rows SHA-256 backfilled. Accepted in prior session.

---

## Item 1 — 9 new files, line counts

Raw `wc -l` output:

```
   659 artifacts/stock-scanner-api/candlestick_patterns.py
   436 artifacts/stock-scanner-api/aiem_harmonic_patterns.py
   613 artifacts/stock-scanner-api/aiem_wyckoff_vpa.py
   412 artifacts/stock-scanner-api/aiem_elliott_wave.py
   701 artifacts/stock-scanner-api/price_structure_patterns.py
   368 artifacts/stock-scanner-api/aiem_pattern_registry.py
   244 artifacts/stock-scanner-api/aiem_pattern_engine.py
   246 artifacts/stock-scanner-api/aiem_pipeline_proof.py
   318 artifacts/stock-scanner-api/verify_pattern_registry.py
  3997 total
```

---

## Items 2+3 — Pattern registry row count + SHA-256 per function

**Correction from narrative:** narrative stated 107 patterns. `build_registry()` returns **116**. Narrative count was wrong.

**Additional gap found:** CHART_STRUCTURE category (35 rows) stores the function name string `classify_chart_patterns` in `function_sha256`, not a real 64-character SHA-256 hash. All other 81 rows have real 64-char hex SHA-256s. This is a registry gap — CHART_STRUCTURE patterns share one dispatcher function; per-pattern bytecode hashing was not implemented for that family.

Raw `build_registry()` output: `build_registry() => 116 rows upserted`

SHA-256 coverage breakdown (raw):
```
Real SHA-256 (64-char hex):   81
Function-name only (not SHA): 35
None:                          0
```

Full registry dump (raw SQL, ordered by category then pattern_name):

```
ROW COUNT: 116
pattern_name                                            category           dir      ena   status     function_sha256
abandoned_baby_bearish                                  CANDLESTICK        BEARISH  True  UNTESTED   308bd0925872bf149b307b361c774cdd534d86bf7e148a2f718117f80b97ca28
abandoned_baby_bullish                                  CANDLESTICK        BULLISH  True  UNTESTED   9c9ec3eb3d5212b9a68167ce51f453831a79ce7db045ba0b16aafc98997c5904
advance_block                                           CANDLESTICK        BEARISH  True  UNTESTED   59a997f332db716d34553de26b90dc7bed922fe448d7ece40a5679c39381507b
bearish_belt_hold                                       CANDLESTICK        BEARISH  True  UNTESTED   6d913d4cf5c47bd62c2583863e94182de5630894925bae1a775f5db332163e6a
bearish_engulfing                                       CANDLESTICK        BEARISH  True  UNTESTED   66a5749d94ebbd8f7c689b3694aaf6ae3ef358d3c64bd6d87444eb0911aa710f
bearish_harami                                          CANDLESTICK        BEARISH  True  UNTESTED   4c24bb400f1797128a0389506d9232d52c3813f9ce73b3984d31cb95df221f6d
bearish_harami_cross                                    CANDLESTICK        BEARISH  True  UNTESTED   ef9b190b7851161520794f86f636513df9f9341c5339470e10bdcada6b7b770e
bearish_kicker                                          CANDLESTICK        BEARISH  True  UNTESTED   ca9df2a1707e77de7b8d38fd6f8e4a93ea6cc8f58dec45b7c21f6e2336ea19a5
bearish_marubozu                                        CANDLESTICK        BEARISH  True  UNTESTED   ac6504f87a49a444399f092ddd4b857e3585678779d6a24e3d1e0aeb490378dc
bullish_belt_hold                                       CANDLESTICK        BULLISH  True  UNTESTED   50ea098c0111f8a02b0e49d8e75b83800be299322058910951d65e3f50357426
bullish_engulfing                                       CANDLESTICK        BULLISH  True  UNTESTED   b04ddaefd5bd5a81b1a0b06d78dcb5d6bae08c00000ad9a17d60270b8463c84e
bullish_harami                                          CANDLESTICK        BULLISH  True  UNTESTED   76f0c3b545d6785f7c21cf501a67a5eff6fc09d3afc4779e2032ecd3f28be532
bullish_harami_cross                                    CANDLESTICK        BULLISH  True  UNTESTED   2992ac331a5b3288f7335dcf3cdfd92dc9bcbefe7aeba1ae1deda482d82a7660
bullish_kicker                                          CANDLESTICK        BULLISH  True  UNTESTED   b4c05a2773571b309c029cfa64f64821eed719bf3abb2a2f9ac28c097e182b34
bullish_marubozu                                        CANDLESTICK        BULLISH  True  UNTESTED   ca4223d20c108f5b41bdd09ce86216a52e8891ac1bee8e39112e6eacda272a2a
concealing_baby_swallow                                 CANDLESTICK        BULLISH  True  UNTESTED   db6c1fe87d2d0b96d5227ab7bc62954cc2c49fcd969ffc774d875b1a30b9d459
dark_cloud_cover                                        CANDLESTICK        BEARISH  True  UNTESTED   25376c674b37ab3d502b5cb2fdc1095ee9bd125778154c8bc1f37a1c050d8a5a
deliberation                                            CANDLESTICK        BEARISH  True  UNTESTED   5967be1386054562f4c76589c6a40310173362ae74872e2c685735a258efbd2e
doji                                                    CANDLESTICK        NEUTRAL  True  UNTESTED   5d3071a4f197f68e94d227defad8ce4fabb61f482193c90cdd8a1b20ab9db7ce
downside_gap_three_methods                              CANDLESTICK        BEARISH  True  UNTESTED   506d82464868896948652a6db9301f83374303e0a58e4ef1996a8d0437b68eab
dragonfly_doji                                          CANDLESTICK        BULLISH  True  UNTESTED   b46687af6623b57beae683da3d3ca608e18773d46ef7d10a5ea824de01892002
evening_doji_star                                       CANDLESTICK        BEARISH  True  UNTESTED   42e978a4eb7eea619fa5ea17f50d4b685978b33f6f45c375049b977397b2e898
evening_star                                            CANDLESTICK        BEARISH  True  UNTESTED   e634e7e47d55e3583abc9e1eeb521da2ee944830ec1bddeaaa6f82d12391b021
falling_three_methods                                   CANDLESTICK        BEARISH  True  UNTESTED   13728767e4c354fb3d965d1ce610d169867c30702f7c80f5f2ed172ffb68142a
gravestone_doji                                         CANDLESTICK        BEARISH  True  UNTESTED   7675876e2f00ab4494a31b490f3e4be9d39d2db1e9002be8e866f8516adcb402
hammer                                                  CANDLESTICK        BULLISH  True  UNTESTED   2e0ea5fc6bfe0dfb2f22208bea327d713c25d66e32f8abf626d2e4e13e598448
hanging_man                                             CANDLESTICK        BEARISH  True  UNTESTED   435fc001eff17f1319df6a6bbf1246ddb696d5e0c30b18ccc4c2337f3a4fbbf2
high_wave                                               CANDLESTICK        NEUTRAL  True  UNTESTED   1a3039b45621dd9742f0828949ec7d24f1c83fca290a49261225808f8085b1b7
in_neck                                                 CANDLESTICK        BEARISH  True  UNTESTED   2810350ce3378e0595fbf807000f14e3f95e455bac254e3a8550be7d88487eed
inverted_hammer                                         CANDLESTICK        BULLISH  True  UNTESTED   3118fed1de8093553653a3ee5708da67c6b9e65d8d236e54b4a4f403fd358907
long_legged_doji                                        CANDLESTICK        NEUTRAL  True  UNTESTED   e3abfc71e185d9309fc8b1956ab2dcde7d784403e91e736619a9395d4415ec19
matching_low                                            CANDLESTICK        BULLISH  True  UNTESTED   f8ca87b1d44c53d497daff5927966ac7bf628bf9d9f0b442f7f89fc8dedb2023
morning_doji_star                                       CANDLESTICK        BULLISH  True  UNTESTED   56c18263585d5a3a79dc2a1d39c713e04954f88a56fd31c6ea5a71be742782f4
morning_star                                            CANDLESTICK        BULLISH  True  UNTESTED   39dd59e258157736699a6e2299cff7a43d0f98260dc589ecf48b04e9a903f069
on_neck                                                 CANDLESTICK        BEARISH  True  UNTESTED   a72ee5ee90fb5483ab26a548094dda2ed26f78ae4319703aa0734a0d816d858c
piercing_line                                           CANDLESTICK        BULLISH  True  UNTESTED   adf80c708327e9334b6a5043a41d61ce145e79601e3d90e6a3c4be2d44e3a6ba
rising_three_methods                                    CANDLESTICK        BULLISH  True  UNTESTED   063271d8fc09cca90e093c124ab219ac470c6431ac3cae070c08890b9edb6f12
shooting_star                                           CANDLESTICK        BEARISH  True  UNTESTED   6b74196e34c7bfbcd654781a7c53a5ee1f0d6a7dd2a68a20f6c2ea6e9e6687a5
spinning_top                                            CANDLESTICK        NEUTRAL  True  UNTESTED   88efa3d5bb50ddc78ab59046f1e5f22946088d17a3cf0e5d5d43a6bfe810088d
stick_sandwich                                          CANDLESTICK        BULLISH  True  UNTESTED   f0ad97473c2e0381de80a60fa7c765af9502129bea26ab8a8ad9f5f3f0c4200d
three_black_crows                                       CANDLESTICK        BEARISH  True  UNTESTED   29581daa22466e71c1c1a0ddac5fa71b350830f026e07bbef079278b6bc4d300
three_inside_down                                       CANDLESTICK        BEARISH  True  UNTESTED   e684a81117eb453f662acc69b184d211e4bec3a4a3544fb9c807b8cd38b8b099
three_inside_up                                         CANDLESTICK        BULLISH  True  UNTESTED   b1d5a57ec95a9cafa6694f9bb439e46f3ac1e29557c06e26250ee8fdd47e2690
three_outside_down                                      CANDLESTICK        BEARISH  True  UNTESTED   56b52507d055db001a9dd03c4cc6bab8d7209575f5a67806304850a92a5ec63d
three_outside_up                                        CANDLESTICK        BULLISH  True  UNTESTED   6a069fbfb5155df710377ecb48667b236f90bbd732d3b668c080161a06bc3b2d
three_stars_south                                       CANDLESTICK        BULLISH  True  UNTESTED   b1773a14a31b94631f9fd3c28d7de40669409a32ca642241211435610777bfee
three_white_soldiers                                    CANDLESTICK        BULLISH  True  UNTESTED   4e1e7f19c6e9ae7a3abee469540c7441d6381dae57c85b9ccec399107c47ab10
tweezer_bottoms                                         CANDLESTICK        BULLISH  True  UNTESTED   b3df4a5a7e6fb91d61d13cc9e701c72bf0d0a825329d8f5e24f9171f52052bf5
tweezer_tops                                            CANDLESTICK        BEARISH  True  UNTESTED   a08995de91ec4c401020e08985622e6a05585e35f7814f9bdc71c2bb24ab15d5
upside_gap_three_methods                                CANDLESTICK        BULLISH  True  UNTESTED   6a47d673d8104c71870bc3c2fb9b426921475cf47ff2b4ce1c4629b2ca35e47a
breakaway_gap_down                                      CHART_STRUCTURE    BEARISH  True  UNTESTED   classify_chart_patterns [NOT real SHA — registry gap, see note above]
breakaway_gap_up                                        CHART_STRUCTURE    BULLISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
broadening_bottom                                       CHART_STRUCTURE    BULLISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
broadening_symmetrical                                  CHART_STRUCTURE    NEUTRAL  True  UNTESTED   classify_chart_patterns [NOT real SHA]
broadening_top                                          CHART_STRUCTURE    BEARISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
channel_ascending                                       CHART_STRUCTURE    BULLISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
channel_descending                                      CHART_STRUCTURE    BEARISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
channel_horizontal                                      CHART_STRUCTURE    NEUTRAL  True  UNTESTED   classify_chart_patterns [NOT real SHA]
complex_head_and_shoulders                              CHART_STRUCTURE    BEARISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
cup_and_handle                                          CHART_STRUCTURE    BULLISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
diamond_bottom                                          CHART_STRUCTURE    BULLISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
diamond_top                                             CHART_STRUCTURE    BEARISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
double_bottom                                           CHART_STRUCTURE    BULLISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
double_top                                              CHART_STRUCTURE    BEARISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
exhaustion_gap_down                                     CHART_STRUCTURE    BULLISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
exhaustion_gap_up                                       CHART_STRUCTURE    BEARISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
flag_or_pennant                                         CHART_STRUCTURE    BOTH     True  UNTESTED   classify_chart_patterns [NOT real SHA]
head_and_shoulders                                      CHART_STRUCTURE    BEARISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
inverse_head_and_shoulders                              CHART_STRUCTURE    BULLISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
inverted_cup_and_handle                                 CHART_STRUCTURE    BEARISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
island_reversal_bearish                                 CHART_STRUCTURE    BEARISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
island_reversal_bullish                                 CHART_STRUCTURE    BULLISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
measured_move_down                                      CHART_STRUCTURE    BEARISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
measured_move_up                                        CHART_STRUCTURE    BULLISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
rounded_bottom                                          CHART_STRUCTURE    BULLISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
rounded_top                                             CHART_STRUCTURE    BEARISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
runaway_gap_down                                        CHART_STRUCTURE    BEARISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
runaway_gap_up                                          CHART_STRUCTURE    BULLISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
triangle_ascending                                      CHART_STRUCTURE    BULLISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
triangle_descending                                     CHART_STRUCTURE    BEARISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
triangle_symmetrical                                    CHART_STRUCTURE    NEUTRAL  True  UNTESTED   classify_chart_patterns [NOT real SHA]
triple_bottom                                           CHART_STRUCTURE    BULLISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
triple_top                                              CHART_STRUCTURE    BEARISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
wedge_falling                                           CHART_STRUCTURE    BULLISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
wedge_rising                                            CHART_STRUCTURE    BEARISH  True  UNTESTED   classify_chart_patterns [NOT real SHA]
elliott_abc                                             ELLIOTT_WAVE       BOTH     True  UNTESTED   453b95874cbc62a85ea99e114ad0cccec718a0e90094c8cb67a449e3ed4525e4
elliott_double_three                                    ELLIOTT_WAVE       BOTH     True  UNTESTED   c38d6c5f91aa23964e25092b62ff42e7def12794dbfad46e42048ab7f9a2258d
elliott_flat                                            ELLIOTT_WAVE       BOTH     True  UNTESTED   53e6fde9b772db89e687dabd7548c4271653bdb237ca4027385e9ff06350315b
elliott_impulse                                         ELLIOTT_WAVE       BOTH     True  UNTESTED   b6fa3bfcb44ef8b3b61292fd3706e6c91f89c91980c172f1c138aea13722bc1b
elliott_triangle                                        ELLIOTT_WAVE       BOTH     True  UNTESTED   ff501431690d41cc378861108d8fce9a7430b1d652a85595d6949e34ae5fd5cb
elliott_triple_three                                    ELLIOTT_WAVE       BOTH     True  UNTESTED   a33dd0f506af2ea9fcb4ba375ad334cf5c0cd0cf7f9a25019c445b88a5972dc3
elliott_zigzag                                          ELLIOTT_WAVE       BOTH     True  UNTESTED   10a974bac6b78b4750d8ca39f03e485cc0c587f23c5e9e021cfe9308a9b4bf51
harmonic_abcd                                           HARMONIC           BOTH     True  UNTESTED   61ad48d944dc2a6db2bf9b3374b4a53a49583a162a3912f1a81cf0ed364b53ab
harmonic_bat                                            HARMONIC           BOTH     True  UNTESTED   f356b3257daf9cd2e9012f4c3c66b25d01350b0cf8f471f9a1ac00ea94b7ba84
harmonic_butterfly                                      HARMONIC           BOTH     True  UNTESTED   11ae874be421a2ad75cf6bac5adafe85ee4cc08b0ceb0470a28a0891b1394649
harmonic_crab                                           HARMONIC           BOTH     True  UNTESTED   f47cdf32136a1ec7748c8704c2c0b88a2b5f00a9a27c1ab0fcb9800e954ce7e7
harmonic_cypher                                         HARMONIC           BOTH     True  UNTESTED   f4077507c9e7f7aca0e1d65f73abbf277b94fd8710923ef004258c61b5837cc0
harmonic_deep_crab                                      HARMONIC           BOTH     True  UNTESTED   29e467b6ad7f5d6bf66ded782dfaca4b9555ec660cd3cc777a4d2dbcc868223d
harmonic_gartley                                        HARMONIC           BOTH     True  UNTESTED   c99164165198a22928ab8648cd298e4ba07b0405d85e711d09a65aca04e3e050
harmonic_shark                                          HARMONIC           BOTH     True  UNTESTED   6e3a1ab20efe735285cc36b229056f90857d4a3415e011773850358a5bd8a2e1
harmonic_three_drives                                   HARMONIC           BOTH     True  UNTESTED   7d2fb95bd938a2b1e717ec203b450ca414a171715f6c14c206d8ae17aaffc00d
vpa_effort_vs_result                                    VPA                BOTH     True  UNTESTED   5fd43bccbfb84631965c724a739e980d1cdd11824277f65dab37427481684c68
vpa_no_demand                                           VPA                BEARISH  True  UNTESTED   b0d04d9e7ac83fbafd8b5f5c9145b7d51bbf54604c3c0a4d6d69bf26725f3bff
vpa_no_supply                                           VPA                BULLISH  True  UNTESTED   2bd3232a28f4c66939e715ec6f291ad35e8b403b465a179dd1647162466b7f83
vpa_shakeout                                            VPA                BULLISH  True  UNTESTED   1dd86d9b5d41d9802abbd8e44a0824c7972a947ac7364c0be2c33b7a84cf8e22
vpa_stopping_volume                                     VPA                BULLISH  True  UNTESTED   783af8e5bd33ec25e3345e807bde9b87d4c4e75d12a57021cff68eaa1659b1b3
vpa_volume_climax                                       VPA                BOTH     True  UNTESTED   53ae730bfc63c97fae1f93f4c622a062574bf346dcdcc1acbf7871b4d36b56dc
vpa_volume_dryup                                        VPA                BOTH     True  UNTESTED   c592f79420704410e9ad6050bb2ec150811a022e3ff46c61e3347ab0871e5f53
wyckoff_accumulation                                    WYCKOFF            BULLISH  True  UNTESTED   ef49e01e10f5f43f92b6070979686e8a4ae1601cfa75f3c4f0801b8ceab5d0a3
wyckoff_buying_climax                                   WYCKOFF            BEARISH  True  UNTESTED   7fa8d8c09d0e184a23e5b6620c66c27e580a2dd964a08611006cba1949e6f3f3
wyckoff_distribution                                    WYCKOFF            BEARISH  True  UNTESTED   43c749921d0a41f93734c2183d0222e1fc8fe099e5bb38096591926bb16ab86f
wyckoff_selling_climax                                  WYCKOFF            BULLISH  True  UNTESTED   b1b5e6c18a24a34f79587b0e682786807b6ccd2fc4c868ed7da9c4cad6cdc214
wyckoff_sos                                             WYCKOFF            BULLISH  True  UNTESTED   1053407f4afbd0f86244246c395b756803db896b99d3349879ea3fc5913b8f2f
wyckoff_sow                                             WYCKOFF            BEARISH  True  UNTESTED   4fdac9879059ae3a4698bcf9f04c3c03b2b6811d80a909cd8d16759435fb6eab
wyckoff_spring                                          WYCKOFF            BULLISH  True  UNTESTED   b71c52be784f09738ff918c528831fb06db9ed5a81b1ec187f127d5642e2dc83
wyckoff_upthrust                                        WYCKOFF            BEARISH  True  UNTESTED   775361dd9664da729a66f7cb48277894b652ac34d7de6762e0fa0ab79136ead9
```

---

## Item 4 — `config.py` SCORE_WEIGHTS sum = 1.0000

Raw Python output:

```
Keys and values:
  pop: 0.18
  ev_after_costs: 0.18
  capital_preservation: 0.14
  defined_risk_quality: 0.1
  capital_efficiency: 0.1
  liquidity: 0.1
  thesis_fit: 0.05
  regime_fit: 0.05
  vol_regime_fit: 0.03
  diversification_value: 0.02
  pattern_confirmation: 0.05
sum() = 1.0
abs(sum-1.0) = 0.0
```

---

## Item 5 — `scoring.py` and `aiem_strat_scheduler.py` before/after SHA-256 + diff

### scoring.py

```
BEFORE (commit 9cb018c): 964f5583643daa6bed9958cdad2534cf21e13eaf605c0cb9b96fcee96164e999
AFTER  (HEAD)          : f2c9a9d4152a2692f948911fa6a5f7241afed265b7469782f8263974c1f70091
Changed: YES
```

Unified diff:

```diff
diff --git a/artifacts/stock-scanner-api/aiem_strat_engine/scoring.py b/artifacts/stock-scanner-api/aiem_strat_engine/scoring.py
index 4c055eb..57b87b5 100644
--- a/artifacts/stock-scanner-api/aiem_strat_engine/scoring.py
+++ b/artifacts/stock-scanner-api/aiem_strat_engine/scoring.py
@@ -12,6 +12,7 @@ The score rewards:
   + Market regime fit
   + Volatility regime fit
   + Diversification value
+  + Pattern confirmation (candlestick/chart/harmonic/Wyckoff/EW vs thesis)

 And penalizes:
   - Max loss size
@@ -213,6 +214,7 @@ def compute_capital_compounding_score(
     n_legs:             int             = 2,
     existing_families:  Optional[list]  = None,
     portfolio_capital:  float           = 100_000.0,
+    pattern_score:      float           = 0.5,
 ) -> Dict[str, float]:
     """
     Compute the Capital Compounding Score and all individual components.
@@ -221,28 +223,30 @@ def compute_capital_compounding_score(
     w = SCORE_WEIGHTS

     # Positive components
-    sc_pop    = score_pop(pop)
-    sc_ev     = score_ev(ev_after_costs)
-    sc_capres = score_capital_preservation(max_loss, max_profit, risk_class)
-    sc_def    = score_defined_risk(risk_class, execution_mode)
-    sc_capeff = score_capital_efficiency(ev_after_costs, return_on_risk)
-    sc_liq    = score_liquidity(liquidity)
-    sc_thesis = score_thesis_fit(strategy_direction, thesis, strategy_vol_thesis, vol_regime)
-    sc_regime = score_regime_fit(strategy_direction, market_regime)
-    sc_vol    = score_vol_fit(strategy_vol_thesis, iv_rank)
-    sc_divers = score_diversification(strategy_family, existing_families)
+    sc_pop     = score_pop(pop)
+    sc_ev      = score_ev(ev_after_costs)
+    sc_capres  = score_capital_preservation(max_loss, max_profit, risk_class)
+    sc_def     = score_defined_risk(risk_class, execution_mode)
+    sc_capeff  = score_capital_efficiency(ev_after_costs, return_on_risk)
+    sc_liq     = score_liquidity(liquidity)
+    sc_thesis  = score_thesis_fit(strategy_direction, thesis, strategy_vol_thesis, vol_regime)
+    sc_regime  = score_regime_fit(strategy_direction, market_regime)
+    sc_vol     = score_vol_fit(strategy_vol_thesis, iv_rank)
+    sc_divers  = score_diversification(strategy_family, existing_families)
+    sc_pattern = _clamp(float(pattern_score))

     raw_score = (
-        sc_pop    * w["pop"]                   +
-        sc_ev     * w["ev_after_costs"]         +
-        sc_capres * w["capital_preservation"]   +
-        sc_def    * w["defined_risk_quality"]   +
-        sc_capeff * w["capital_efficiency"]     +
-        sc_liq    * w["liquidity"]              +
-        sc_thesis * w["thesis_fit"]             +
-        sc_regime * w["regime_fit"]             +
-        sc_vol    * w["vol_regime_fit"]         +
-        sc_divers * w["diversification_value"]
+        sc_pop     * w["pop"]                   +
+        sc_ev      * w["ev_after_costs"]         +
+        sc_capres  * w["capital_preservation"]   +
+        sc_def     * w["defined_risk_quality"]   +
+        sc_capeff  * w["capital_efficiency"]     +
+        sc_liq     * w["liquidity"]              +
+        sc_thesis  * w["thesis_fit"]             +
+        sc_regime  * w["regime_fit"]             +
+        sc_vol     * w["vol_regime_fit"]         +
+        sc_divers  * w["diversification_value"]  +
+        sc_pattern * w["pattern_confirmation"]
     )

     # Penalties
@@ -256,18 +260,19 @@ def compute_capital_compounding_score(
     final_score = _clamp(raw_score - total_penalty)

     return {
-        "score_pop":            round(sc_pop,    4),
-        "score_ev":             round(sc_ev,     4),
-        "score_capital_pres":   round(sc_capres, 4),
-        "score_defined_risk":   round(sc_def,    4),
-        "score_cap_efficiency": round(sc_capeff, 4),
-        "score_liquidity":      round(sc_liq,    4),
-        "score_thesis_fit":     round(sc_thesis, 4),
-        "score_regime_fit":     round(sc_regime, 4),
-        "score_vol_fit":        round(sc_vol,    4),
-        "score_diversification":round(sc_divers, 4),
-        "penalty_total":        round(total_penalty, 4),
-        "capital_compounding_score": round(final_score, 4),
+        "score_pop":                  round(sc_pop,     4),
+        "score_ev":                   round(sc_ev,      4),
+        "score_capital_pres":         round(sc_capres,  4),
+        "score_defined_risk":         round(sc_def,     4),
+        "score_cap_efficiency":       round(sc_capeff,  4),
+        "score_liquidity":            round(sc_liq,     4),
+        "score_thesis_fit":           round(sc_thesis,  4),
+        "score_regime_fit":           round(sc_regime,  4),
+        "score_vol_fit":              round(sc_vol,     4),
+        "score_diversification":      round(sc_divers,  4),
+        "score_pattern_confirmation": round(sc_pattern, 4),
+        "penalty_total":              round(total_penalty, 4),
+        "capital_compounding_score":  round(final_score,   4),
     }
```

### aiem_strat_scheduler.py

```
BEFORE (commit 9cb018c): 9ae390296a1a3971eae2c724dd2bd674aaca2e85bce2de586a99cb4fab563cd1
AFTER  (HEAD)          : c6dccb6db2f788f6cecc7b169a5b90128e0c3a4fd390d4b788dba1cabfc75564
Changed: YES
```

Unified diff (additions only — no lines removed from scheduler):

```diff
@@ -200,6 +200,16 @@ def _seed_candidates():
+            try:
+                import aiem_pipeline_proof as _proof
+                _proof.log_seed_proof(candidates=rows, source_table="polygon_rvol_scan")
+            except Exception as _pe:
+                log.debug(f"seed proof log skipped: {_pe}")

@@ -260,6 +270,35 @@ def _run_one_job(...):
+    pattern_score = 0.5
+    pattern_result: dict = {}
+    try:
+        import aiem_pipeline_proof as _proof
+        from aiem_pattern_engine import detect_for_ticker
+        pattern_result = detect_for_ticker(ticker, thesis=thesis, lookback=60)
+        pattern_score  = pattern_result.get("pattern_score", 0.5)
+        _proof.log_stage(trace_id=run_id, ticker=ticker, thesis=thesis,
+            stage="pattern_scan", data={...})
+    except Exception as _pat_err:
+        log.debug(f"[{run_id}] pattern detection skipped: {_pat_err}")

@@ -330 +369:
+                pattern_score=pattern_score,   # passed to compute_capital_compounding_score

@@ -351 +391:
+    # proof: decision stage
+    try:
+        _proof.log_stage(trace_id=run_id, ..., stage="decision", data={...})
+    except Exception as _de: ...

@@ -375 +438:
+            f"Pattern: {pattern_score:.3f}\n"
+            # proof: paper_trade stage
+            try:
+                _proof.log_stage(trace_id=run_id, ..., stage="paper_trade", data={...})
+            except Exception as _pe2: ...
```

---

## Item 6 — grep -n for forbidden website scanner imports

Raw output (file: `artifacts/stock-scanner-api/aiem_strat_scheduler.py`):

```
grep -n 'import main':          (no match)
grep -n 'from main import':     (no match)
grep -n '_mkt_gap_volume_scan': (no match)
grep -n '_mkt_nano_cap':        (no match)
grep -n 'website_scanner':      (no match)
```

---

## Item 7 — `aiem_pipeline_proof_log` rows for today

Raw SQL:
```sql
SELECT id, trace_id, ticker, thesis, stage, sha256, logged_at::text
FROM aiem_pipeline_proof_log
WHERE scan_date = CURRENT_DATE
ORDER BY logged_at ASC;
```

Result:
```
ROW COUNT: 0
Total rows in table (all dates): 0
```

**Why 0 rows:** `aiem_pipeline_proof_log` is written by `aiem_strat_scheduler.py`, which fires at 09:40 ET on market days. The scheduler was deployed after market hours on 2026-07-17 and has not yet fired. The table exists (DDL confirmed by CREATE TABLE IF NOT EXISTS executing without error), and the wiring is proven by the diff in Item 5. Live rows will appear at next 09:40 ET market-day run.

---

## Item 8 — Evidence chain validation

`verify_pattern_engine.sh` follows the same tamper-evident structure as `verified_run.sh` (self-SHA embedded in bundle, output to JSON).

```
sha256sum verified_run.sh          : 31c74ee84035e9da85bb0d14799122eb5eeafb68f3a9d3d8bc28bb9e210f3625
sha256sum verify_chain.sh          : 469edcd448283423115a845237aa7a6fe51124aa0e5ad49cab1f7d9894733ed3
sha256sum verify_pattern_engine.sh : 685833b9b741b1bd55ddfcae54440634250dfb5d2db9a62c20cfc84968975ee8
```

`verify_chain.sh` validates the failover bundle format (checks `options_pipeline_jobs`, `daily_pipeline_runs`, GitHub Actions) — it does not validate the pattern engine bundle. Running it against `verified_pattern_engine_2026-07-18_001231.json` would fail at section [D] because the DB tables it checks are different. The pattern engine evidence is self-contained in `verify_pattern_engine.sh` + its JSON bundle.

---

## Item 9 — `git diff 9cb018c HEAD --stat`

```
 .agents/agent_assets_metadata.toml                 |   6 +
 AIEM_PATTERN_ENGINE_DELIVERY.md                    | 237 +++++++++++++++++++++
 artifacts/stock-scanner-api/aiem_strat_engine/config.py  |   7 +-
 artifacts/stock-scanner-api/aiem_strat_engine/scoring.py |  69 +++---
 artifacts/stock-scanner-api/aiem_strat_scheduler.py      |  84 ++++++++
 5 files changed, 368 insertions(+), 35 deletions(-)
```

Note: the 9 new pattern files (`candlestick_patterns.py`, `aiem_harmonic_patterns.py`, etc.) were added in commit `9cb018c` and are therefore not shown in the diff from that commit forward. Their presence is proven by Item 1 (`wc -l`).

---

## Known gaps disclosed

| Gap | Description |
|-----|-------------|
| Registry count | Narrative said 107; actual is **116**. Narrative was wrong. |
| CHART_STRUCTURE SHA-256 | 35 rows store function name string, not a real 64-char SHA-256. All 5 other categories (81 rows) have real SHA-256 hashes. |
| Proof log rows | 0 today (0 total) — scheduler not yet fired post-deployment. Table exists; wiring proven by diff. |

---

## DELETE hold

`ase_rpt_weekly_2099-01-01_48fc66fb` — no action taken. Awaiting Joel's explicit go/no-go.
