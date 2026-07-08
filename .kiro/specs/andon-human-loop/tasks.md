# Implementation Tasks — andon-human-loop

> ステータス: **tasks-generated** ／ 言語: ja
> 記法: 2階層・`(P)`=並行実行可・末尾は充足する要件番号（numeric）
> 出典: [requirements.md](requirements.md) / [design.md](design.md) / [research.md](research.md)
> `- [ ]*` = MVP後に延期可の任意テストサブタスク

---

## 1. 基盤（契約・設定・スキーマ）

- [x] 1.1 pydantic 契約を追加（CauseCategory / Actor / RoutingDecision / EscalationStep / NotificationRecord、RcaResult に `category` 既定 "other"）— 4.1, 5.1 ✅TDD 5テスト緑
- [x] 1.2 設定注入を追加（SlackConfig: bot_token / signing_secret / channel_id、EscalationConfig: tier2=300s / tier3=900s。未設定なら Slack 無効）— 10.3, 10.6 ✅send_enabled=False確認
- [x] 1.3 スキーマ増分（notifications / routing_rules / escalations 新設、rca_results.category・feedback.actor_* ・past_cases.attachment_uri 追加）を schema.sql と起動時自己マイグレーション（migrations.py、_restore_state 配線）の両方に実装 — 1.5, 4.4, 5.5, 9.2, 10.2
- [x] 1.4 routing_rules のシード投入（positioning / conveyance / sensor / other → 保全担当・班長・ベンダー窓口のデモ当番表。mention は実IDに UPDATE で差替）— 5.4, 5.5

## 2. 裁定の単一パス化（全入口の合流点）

- [x] 2.1 `/feedback` の本体を `_record_verdict(event_id, verdict, actor, human_cause)` に抽出し、既存 dashboard 経路を actor(surface="dashboard") 付きで通す — 2.2, 4.2
- [x] 2.2 裁定済みイベントへの再操作を検出し、二重記録せず既裁定情報（裁定者・時刻・結果）を返す（`feedback_store.get_verdict`）— 2.4
- [x] 2.3 feedback 保存に actor_surface / actor_id / actor_name を記録（DB INSERT/SELECT 拡張・JSONL は全キー保存・ts 付与）— 4.1, 4.2, 4.4
- [x] 2.4 単一パスのテスト（dashboard 裁定の actor 記録・二重裁定の prior 返却・slack 面の同一ストア書込）— 2.2, 2.4, 4.1 ✅ATDD 3テスト、全17緑

## 3. 通知シンク抽象と Slack 送信

- [x] 3.1 `NotificationSink` Protocol と `NullSink` を実装（enabled / post_card / update_card / post_thread。未設定環境は no-op + ログ）— 1.1, 10.6
- [x] 3.2 `SlackSink` を実装（Block Kit カード: 真因候補・確信度・根拠・deep link・裁定2ボタン・一次担当メンション。client 注入可）— 1.2, 1.3, 2.1, 5.3
- [x] 3.3 冪等化: notif_store（notifications テーブル / JSONL 両輪・by_message_ts 相関つき）を投稿前参照、失敗時はキー未消費 — 1.5
- [x] 3.4 送信失敗の可視化（構造化ログ + state.sink_error → SSE 経由でダッシュボードバナー表示。SSE 通知は常に先行）— 1.4, 10.5
- [x] 3.5 `_notify_stop` にシンク呼び出しを配線（`_post_card`。routing は task 4 で結線、現状 None。RCA 未完時は SSE のみ）— 1.2
- [x] 3.6 送信系テスト（FakeClient: カード材料・冪等×再起動・失敗の loud 化・update_card 裁定反映）— 1.2, 1.4, 1.5, 2.5 ✅BDD/TDD 5テスト、combined 22緑

## 4. 真因カテゴリとルーティング（決定論）

- [x] 4.1 RCA プロンプトの JSON 出力に `category`（enum 4値）を追加し、`infer()` で `normalize_category`（shared）による語彙検証・不正値 "other" 正規化 — 5.1, 5.2
- [x] 4.2 `routing.resolve(event_id, category)` を実装（routing_rules JOIN のみ・DB なしは in-code フォールバック・未登録は既定=班長＋WARN 記録・RoutingDecision を構造化ログで監査記録）＋ `_post_card` に結線 — 5.2, 5.3, 5.4, 5.6
- [x] 4.3 ルーティングのテスト（正規化6ケース・カテゴリ→mention・未登録→既定先・監査材料の完全性）— 5.2, 5.4, 5.6 ✅TDD 4テスト
- [x] 4.4 (P) `test_rca.py`（手動 Vertex 統合）にカテゴリ検証を追加。オフラインの enum 正規化は test_routing でカバー — 5.1 ※実呼び出しは task 11.3 で実施

## 5. エスカレーションエンジン

- [x] 5.1 `EscalationEngine` を実装（tick・fire_at×未裁定の決定論判定・schedule / cancel。ストア = escalations テーブル / JSONL 両輪）— 6.1, 6.2, 6.4, 6.6
- [x] 5.2 発火時の通知（tier2: スレッド+班長メンション、tier3: ベンダー連絡先をスレッド+ダッシュボード on_notice 提示・自動発報なし）と全発火の構造化ログ監査 — 6.2, 6.3, 6.5
- [x] 5.3 復元は「毎 tick ストア再読」で構造的に担保（プロセス再起動＝そのまま復元。新インスタンスの発火をテストで確認）— 10.2
- [x] 5.4 訂正対話の 30 分未確定クローズを同 tick で処理（touch/close_correction を /correct に配線・スレッドに継続方法提示・一度だけ）— 3.5
- [x] 5.5 fake clock テスト（発火順序・単発性・裁定/明示取消・再起動復元・訂正タイムアウト）— 6.1, 6.2, 6.3, 6.4, 6.6, 10.2 ✅TDD 6テスト、計32緑。裁定成立時の cancel+カード更新 `_after_verdict` も配線済み（6.2 の一部前倒し）

## 6. Slack 受信（署名検証・裁定ボタン）

- [x] 6.1 `/slack/interactivity` と `/slack/events` ルートを実装（署名検証 per-request・5分窓・失敗 401+WARN・未設定 503・url_verification・即 ACK→asyncio 本処理・handler 注入で循環 import 回避）— 10.4, 10.6
- [x] 6.2 block_actions「正しい」→ `_record_verdict`(actor=Slack ID/表示名) → `_after_verdict`（update_card+エスカレーション取消、task 5 で配線済み）。「違う」→ on_wrong フック（task 7 で訂正対話に接続）— 2.1, 2.2, 2.5, 4.1, 6.4
- [x] 6.3 裁定状態の両面同期（`/events` に最新 verdict+actor を付与。Slack 側は update_card、既裁定は `_record_verdict` の prior 返却）— 2.3, 2.4
- [x] 6.4 フィクスチャテスト（HMAC 実計算・不正署名/古い ts 401・challenge 応答・ボタン→単一レコード actor=slack・再送冪等・secret なし 503）— 2.1, 2.4, 10.4, 10.6 ✅ATDD 7テスト、計39緑

## 7. Slack スレッド訂正対話

- [x] 7.1 スレッド返信の対応付け（`_slack_on_message`: thread_ts == notifications.message_ts・bot はルート層で除外・無関係スレッド無視）→ `elicit_correction`(user_id=Slack ID, actor 伝搬) → 応答を post_thread — 3.1, 3.2
- [x] 7.2 「違う」ボタン → `_slack_on_wrong`（既裁定ガード付き・空メッセージ起動・touch_correction でタイムアウト監視開始）— 3.1
- [x] 7.3 訂正確定時のスレッド要約（確定 cause 復唱＋次回反映の一言）＋ update_card("wrong")＋エスカレーション取消。recorder に actor 伝搬（Slack/dashboard 両面の訂正が監査に帰属）— 3.3, 3.4
- [x] 7.4 スレッド訂正の結合テスト（開始対話・返信→記録→要約・無関係スレッド無視。Vertex モック）— 3.2, 3.3 ✅BDD 3テスト、計42緑

## 8. モバイル裁定ページ（deep link 先）

- [x] 8.1 (P) `GET /e/{event_id}` + `static/event.html` + `GET /api/event/{id}`（1カラム 390px・52px 裁定ボタン・1画面目に真因候補/確信度/根拠/代表フレーム/エスカレーション表示）— 8.1, 8.2, 8.3
- [x] 8.2 「違う」→ ページ内訂正チャット（`/correct` 再利用・suggestions チップ・記録で裁定済み化）。`/correct` に裁定済みガード追加（面間一貫性）＋メイン UI の already_adjudicated 応答処理 — 3.2, 8.3, 2.4
- [x] 8.3 俯瞰ダッシュボードは非変更（fb() の応答処理と user_id 帰属のみ修正。実ブラウザで stream 動作を回帰確認 — 三項演算子の構文エラーを検出・修正）— 8.4
- [x] 8.4 ページテスト（viewport/裁定 HTML 契約・/api/event 材料+verdict 遷移・404・/correct ガード）＋実ブラウザ 375px 検証（実 Vertex RCA 表示・裁定済み状態）— 8.1, 8.3 ✅計46緑

## 9. 現場写真の添付とマルチモーダル還流

- [ ] 9.1 `attachments_store` を実装（GCS 非公開保存・`/attachment/{event_id}` プロキシ・image/* かつ 10MB 検証）— 9.2, 9.5, 9.6
- [ ] 9.2 モバイル訂正ページに写真添付（`POST /correct/attachment`。スキップ可）— 9.1, 9.4
- [ ] 9.3 Slack スレッド画像の取り込み（files:read → url_private 取得 → attachments_store）— 9.1
- [ ] 9.4 訂正確定時に past_cases.attachment_uri へ紐づけ、`search_past_cases` の返却に含める — 9.2
- [ ] 9.5 `infer()` で top-1 ヒットに写真がある場合のみ画像 Part を追加（Vertex マルチモーダル）— 9.3
- [ ]* 9.6 添付経路のテスト（非画像/超過サイズ拒否・添付なし訂正の完結・top-1 のみ還流）— 9.3, 9.4, 9.5

## 10. 分析ビュー

- [ ] 10.1 (P) 集計 API（`/analytics/pareto`・`/analytics/accuracy`・`/analytics/recurrence`。訂正後カテゴリ優先・open は既定停止時間でクリップ・データなしは empty 明示）— 7.2, 7.3, 7.5, 7.7
- [ ] 10.2 再発検知（7日×同一カテゴリ≥3 で alert＋定型 suggestion。閾値判定は決定論）— 7.4
- [ ] 10.3 `static/analytics.html`（パレート棒+累積折れ線・正答率折れ線をインライン SVG・期間切替 7/30日）— 7.2, 7.3, 7.5
- [ ] 10.4 サイドメニューの「準備中」項目を分析ビュー導線に置換＋グラフ→イベント一覧ドリルダウン（`/events` に category/期間フィルタ追加）— 7.1, 7.6
- [ ] 10.5 集計関数のフィクスチャテスト（パレート順序・累積比・空期間・再発閾値）— 7.2, 7.4, 7.7

## 11. デプロイ配線と統合検証

- [ ] 11.1 Slack アプリ作成（scopes: chat:write / files:read / users:read、Event 購読 message.channels、Interactivity URL 設定）＋ Secret Manager に bot_token / signing_secret 登録 → deploy.yml で env 注入 — 10.3
- [ ] 11.2 CI に新テストを組込み（Slack なしで全テスト成立を確認）— 10.6
- [ ] 11.3 本番 E2E 検証（実 Slack チャネルで: 停止→カード＋メンション→ボタン裁定→カード更新→スレッド訂正→past_cases 還流→再発時の推論変化、エスカレーション発火はデモ用短縮タイマーで確認）— 1.2, 2.1, 3.3, 6.2
- [ ] 11.4 ドキュメント更新（README アーキ図に Slack 面を追加・docs/audit.md にルーティング/エスカレーション監査・docs/observability.md に新ログ）— 5.6, 6.5, 10.5
- [ ]* 11.5 min/max-instances=1 のままタイマー系が動作することの復元テスト（リビジョン入替→escalations 復元）— 10.2

---

## 実施順の目安

- 直列の背骨: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 11
- (P) 並行可: 8（モバイル）と 10（分析）は 2 完了後いつでも。4.4 は 4.1 完了後いつでも
- 9（写真）は 7 と 8 の後
