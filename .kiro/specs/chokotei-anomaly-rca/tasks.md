# Implementation Tasks — chokotei-anomaly-rca

> ステータス: **tasks-generated** ／ 言語: ja
> 記法: 2階層・`(P)`=並行実行可・末尾は充足する要件番号（numeric）
> 出典: [requirements.md](requirements.md) / [design.md](design.md) / [research.md](research.md)
> `- [ ]*` = MVP後に延期可の任意テストサブタスク

---

## 1. プロジェクト基盤とスキーマ

- [x] 1.1 モノレポ骨格を作成（`services/{detector,agent,dashboard}` `packages/shared` `infra/`）— 10.x
- [x] 1.2 `packages/shared` に pydantic 契約を定義（PartObservation/FrameResult/FlagDetail/AnomalyEvent/RcaResult/Feedback/IoTReading）— 2, 3, 5, 6, 8 ✅import検証済み
- [x] 1.3 共通設定注入（閾値 offset=10/angle=10/gap=1.5/band=8–12、ROI、リージョン）を env/設定ファイル化 — 2, 10.6
- [x] 1.4 依存を固定（`opencv-python-headless` `google-adk==2.3.0` `asyncpg` `sse-starlette` `fastapi` 等）— 5, 10.3

## 2. データ基盤（Cloud SQL）

- [ ] 2.1 Cloud SQL Postgres インスタンス作成＋pgvector 有効化、`roles/cloudsql.client` 付与 — 10.2, (研究#5)
- [ ] 2.2 アプリ表のマイグレーション（anomaly_events / iot_readings / rca_results / feedback / past_cases）— 3, 4, 5, 8, 9
- [ ] 2.3 **ADK セッション永続の Day1 smoke**：`SESSION_DB_URL=postgresql+asyncpg://…` で `DatabaseSessionService` 初期化・往復確認（de-risk #1）— 5.5
- [ ] 2.4 iot_readings に `(ts, channel)` インデックス、past_cases に vector index（次元確認後 HNSW/IVFFlat）— 4.4, 9

## 3. 検知エンジン（detector）

- [x] 3.1 PoC（`docs/poc/detect_v2.py`）を `services/detector` の再利用モジュールへ昇格（`detect_frame -> FrameResult`）— 2.1, 2.2, 2.3, 2.4
- [x] 3.2 判定根拠の構造化記録（FlagDetail に数値・シグナル種別）— 2.7, 10.4
- [x] 3.3 時系列トラッキングで同一変位部品を1 `AnomalyEvent` に集約（新規確定で一度だけ発火、閾値未満継続で close）— 3.1, 3.2, 3.3, 3.4
- [ ] 3.4 代表フレームを Cloud Storage(private) に保存し `rep_frame_uri` を格納 — 3.2, 5.2  ⏸cloud
- [ ] 3.5 境界帯のみ Gemini Vision で確認（`in_confirm_band` は実装済、実呼び出しは残）— 2.5, (研究#4)
- [x] 3.6 (P) 検出部品<4 のフレームを対象外化＋記録 — 2.6
- [x] 3.7 トラッキングと閾値判定のテスト（正常でfalse positiveゼロ／異常イベント1件）— 2, 3 ✅241f/first139/1event/peak17.5

## 4. 擬似ストリーム配信

- [x] 4.1 映像を5fpsサンプリング＋末尾ループ再生する配信ループ — 1.1, 1.2
- [x] 4.2 検知オーバレイ（ベースライン・部品マーカー・異常マーカー）を重畳 — 1.3
- [x] 4.3 SSE エンドポイント `GET /stream` でフレーム逐次配信 — 1.4 ✅稼働確認
- [x] 4.4 映像ソース読み込み失敗時のエラー記録＋停止状態表示（`/healthz` video_present）— 1.5

## 5. 合成IoTデータ

- [x] 5.1 (P) 映像タイムラインに整合した合成IoT生成（vibration_x/y/z, temperature, motor_current）— 4.1
- [x] 5.2 異常窓にX軸加速度スパイク＋高調波を相関注入 — 4.3 ✅X軸18倍/max3.67G
- [x] 5.3 生成データを永続化＋時刻/チャネル照会関数（P1はローカルJSONL、Cloud SQL差替は後）— 4.2, 4.4

## 6. 原因推定エージェント（ADK）

- [x] 6.1 ADK root orchestrator を構成（`google-adk==2.3.0`、InMemory/DatabaseSessionService 切替）— 5.1, 5.5
- [x] 6.2 FunctionTool 実装：`query_vibration` / `query_logs` / `get_frame`（AgentTool構成、transfer不使用）— 4.2, 5.2, 6.2
- [x] 6.3 `search_past_cases`（P1はローカルJSONL＋keyword、pgvectorは後）— 5.4, 9.1, 9.2 ✅治具緩みヒット
- [x] 6.4 `infer(event) -> RcaResult`（原因候補・確信度・根拠を生成）— 5.1, 5.3 ✅実Vertex, conf0.6
- [x] 6.5 モデル呼び出し失敗を例外＋構造化ログ化／DB永続は SESSION_DB_URL で有効化（DB smokeは2.3待ち）— 5.6, 10.5
- [x] 6.6 推論の結合テスト（相関スパイク vibration_x=3.559 を根拠に採用）— 5 ✅緑

## 7. ダッシュボード＋チャット（FastAPI＋軽量フロント）

- [x] 7.1 映像(SSE)＋IoT時系列を同一画面に表示するUI（統合サーバ server.py）— 1.4, 7.1 ✅実動
- [x] 7.2 異常イベントの視覚強調＋直近一覧（`GET /events`）＋新規異常で自動RCA＋通知 — 7.2, 7.3
- [x] 7.3 IoT照会 API `GET /iot?channel&t0&t1` とUI連携（振動チャート）— 6.2, 7
- [x] 7.4 チャット `POST /chat`：異常通知の受信表示＋対話照会（実Vertexでログ照会）— 6.1, 6.2, 6.3 ✅温度平均回答
- [x] 7.5 範囲外照会時の「データ無し」明示（/iot found:false＋chat指示）— 6.4

## 8. HITL 確認・学習ループ

- [ ] 8.1 `POST /feedback`：正誤判定＋（誤り時）正しい原因入力（human_cause必須検証）— 8.1, 8.2
- [ ] 8.2 (AI推定/人手判定/正しい原因/時刻) を feedback 表へ蓄積 — 8.3, 8.4
- [ ] 8.3 `wrong` 事例を past_cases に埋め込み登録し次回 few-shot に還流 — 9.1, 9.2
- [ ] 8.4 正誤率などの品質指標をダッシュボード表示 — 9.3

## 9. 配線・デプロイ（稼働URL）

- [ ] 9.1 各サービスの Dockerfile／Cloud Run 設定（min-instances=0）— 10.1, 10.2
- [ ] 9.2 Cloud Run へ `--add-cloudsql-instances`／Vertex は ADC、シークレット非コミット確認 — 10.2, 10.3, (研究#5)
- [ ] 9.3 ローカル `gcloud run deploy --source .` で2サービス公開（WIFは範囲外）— 10.1
- [ ] 9.4 Logging/Monitoring/Trace への主要処理ログ・メトリクス出力 — 10.5
- [ ] 9.5 **閉ループ E2E スモーク**：映像→検知→推定→チャット通知→HITL→蓄積が稼働URLで通ること — 1〜10

## 10. 監査・ガードレール確認

- [ ] 10.1 重い判定が決定論側（CV）にあることの確認と監査ログ経路の検証 — 10.4
- [ ] 10.2* リージョン規定（モデル=us-central1／基盤=asia-northeast1）の設定テスト — 10.6

---

## カバレッジ

要件 1〜10 の全 acceptance criteria をタスクに割当済み。P1 完了条件＝**9.5 の E2E スモークが稼働URLで通ること**。
`(P)` は独立実装可（3.6, 5.1 など）。実装は `spec-impl` で1タスクずつ、コンテキストを都度クリアして進める。
