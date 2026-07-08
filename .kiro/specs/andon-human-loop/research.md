# Research Log — andon-human-loop

> 種別: **Extension**（稼働中システムへの統合）→ integration-focused discovery
> 日付: 2026-07-08 ／ 言語: ja

## Summary

- 既存コードは「単一 FastAPI アプリ（dashboard）に detector/agent モジュールを同居」したステートフル・シングルトン。
  通知・裁定・訂正・学習還流の**全コードパスが `services/dashboard/server.py` に集約**されており、
  本フィーチャーは (a) 通知の出口を抽象化して Slack を足す、(b) 裁定・訂正の入口を Slack からも受ける、
  (c) 集計 API と画面を足す — の3点で既存パスを**再利用**できる（新規ドメインロジックはルーティングとエスカレーションのみ）。
- Slack 受信は **HTTP モード（Events API + Interactivity）＋署名検証**が現行標準。
  verification token は非推奨。イベント/操作への **3秒以内 ACK** が必須 → 「即200 → asyncio で非同期処理」の
  既存パターン（`asyncio.create_task`）がそのまま適用できる。
- エスカレーションタイマーは**プロセス内 asyncio ＋ DB 永続（起動時復元）**を採用。
  既存の cold-start restore パターン（`_restore_state`）と同型で、シングルトン前提と整合する。

## Research Log

### R-1: 既存の統合点（コード調査）

| 統合点 | 場所 | 本フィーチャーでの扱い |
|---|---|---|
| 停止通知の生成 | `server.py::_notify_stop`（テキスト組立→ `notifs` キュー） | 通知シンク抽象の呼び出し点。SSE キューと Slack へ**同じ材料**を配る |
| HITL 裁定 | `POST /feedback`（verdict 記録） | 共有関数に抽出し、Slack ボタンハンドラからも呼ぶ（単一の真実） |
| 訂正対話 | `POST /correct` → `elicit_correction`（(user,event) 毎セッション） | Slack スレッド返信を同じ関数に流す。`user_id` に Slack ID を渡すだけでセッション分離が成立 |
| 学習還流 | `server.py::_persist_correction`（feedback + past_cases + cache 無効化） | 変更不要。Slack 経路もここに合流 |
| RCA 出力 | `rca_agent.py::infer` → `RcaResult` | `category` フィールド追加（閉じた語彙）— ルーティングの材料 |
| 監査・ログ | `chokotei_shared.obs` 構造化ログ + Cloud SQL 各テーブル | actor（面・ID・表示名）カラム追加、`notifications`/`escalations` テーブル追加 |
| 正答率 | `feedback_store.metrics()` / `GET /metrics` | 時系列化（created_at で集計）して分析ビューへ |
| 代表フレーム | `frames_store`（GCS 非公開 + `/frame/{id}` プロキシ） | 添付写真ストアは同パターンを複製（`attachments_store`） |
| 設定 | `chokotei_shared/config.py`（env 注入 dataclass） | `SlackConfig` / `EscalationConfig` を同形式で追加 |

**含意**: 新サービスは増やさない。dashboard アプリに `/slack/*`・`/analytics/*`・`/e/{event_id}` を足す。

### R-2: Slack 受信方式 — HTTP モード vs Socket Mode

- **採用: HTTP モード**（Events API + Interactivity、`/slack/events`・`/slack/interactivity`）
  - dashboard は既に公開 HTTPS（Cloud Run）— 受信 URL がタダで手に入る
  - 署名検証（`X-Slack-Signature` HMAC-SHA256 + timestamp 5分窓）で真正性担保（R10.4）。
    verification token 方式は非推奨（[Slack docs](https://docs.slack.dev/changelog/)）
  - リクエスト/レスポンスが純粋な HTTP → **録画ペイロードでの回帰テストが容易**（CI で Slack 不要 = R10.6）
- **不採用: Socket Mode** — 常駐 WebSocket の面倒（再接続監視）をシングルトンに持ち込む。
  公開エンドポイントを既に持つ本構成では利点がない
- **不採用: Bolt for Python** — ルート2本のために独自ミドルウェア層を導入する価値が薄い。
  `slack_sdk`（WebClient + SignatureVerifier）のみを直接使い、FastAPI 統一を保つ
- **制約**: イベント・ボタン操作は **3秒以内に ACK** 必須。Slack はタイムアウト時に**最大3回再送**
  → `X-Slack-Retry-Num` ヘッダ + イベント ID で冪等化（R1.5 の重複防止と同じ機構に載せる)

### R-3: エスカレーションタイマー

- **採用: プロセス内 asyncio 周期タスク（10秒 tick）＋ `escalations` テーブル永続・起動時復元**
  - シングルトン（max-instances=1）なので二重発火の分散問題が存在しない
  - 再起動は `_restore_state` と同じ起動フックで `fire_at` 未来分を復元（R10.2）
  - 発火判定は「時刻×裁定状態」の決定論（R6.6）
- **不採用: Cloud Tasks / Cloud Scheduler** — 外部から叩く HTTP エンドポイントの認証設計・
  IAM 配線が増える。数分粒度のタイマー2段に対して過剰。事例が増えて detector を
  worker pools に切り出す時点で再検討（design の分割線に記載)

### R-4: 真因カテゴリの決定論性（R5.2 との整合）

- RCA プロンプトの JSON 出力に `category` を追加するが、**閉じた語彙（enum）**
  `positioning | conveyance | sensor | other` に制限し、サーバ側で語彙検証
  → 不一致・欠落は `other` に正規化 → 既定通知先（班長）へ（R5.4）
- つまり LLM は「enum から1つ選ぶ材料提供」まで。**通知先の解決はテーブル JOIN のみ**
  （`routing_rules`: category → mention 先・段構成。DB 保持で再デプロイ不要 = R5.5）
- 後付け分類（kind→category の固定写像）も検討したが、kind（offset/rotation/gap）は
  症状であり真因軸と直交しないため、RCA 出力に含める案を採用

### R-5: 写真のマルチモーダル還流

- Slack スレッドの画像: `files:read` スコープ + `url_private` を Bot トークンで取得 → GCS 保存
  （公開 URL にしない = R9.6。`/frame/{id}` と同じプロキシ方式で提示）
- `past_cases.attachment_uri` カラム追加。検索ヒット時の還流は **top-1 の写真のみ**を
  Gemini マルチモーダル入力に含める（コンテキスト膨張と課金を抑制。効果が出たら拡張）

### R-6: 分析ビューの描画

- 既存フロントは「素 JS + サーバレンダ」— チャートライブラリ導入より **インライン SVG 描画**
  （パレート: 棒+折れ線、正答率: 折れ線）が依存ゼロで整合的。集計はサーバ側 SQL（`/analytics/*` JSON）
- 損失分の算出: `anomaly_events` の `started_ts..ended_ts`（open のまま終わるデモイベントは
  既定停止時間でクリップ）× 期間集計。カテゴリは `rca_results.category`（裁定で訂正されたものは
  訂正後カテゴリを優先）

## Design Decisions（要約）

| # | 決定 | 根拠 |
|---|---|---|
| D1 | 通知シンクは Protocol 抽象 + SlackSink 実装、未設定時は no-op（ログのみ） | R1.1 / R10.6 |
| D2 | Slack 受信は HTTP + 署名検証 + 即ACK非同期処理 | R-2 |
| D3 | 裁定・訂正は既存コードパスへ合流（Slack は入口が増えるだけ） | R2.2 / R3.3 |
| D4 | カテゴリは閉じた enum、ルーティングはテーブル JOIN のみ | R5.2 / R-4 |
| D5 | タイマーはプロセス内 + DB 復元 | R-3 / R10.2 |
| D6 | 分析はサーバ集計 + インライン SVG | R-6 |
| D7 | 写真は GCS 非公開 + top-1 マルチモーダル還流 | R-5 |

## Risks

| リスク | 影響 | 緩和 |
|---|---|---|
| Slack 3秒 ACK 超過 → 再送 → 重複処理 | 裁定二重記録・カード重複 | 即200 + `X-Slack-Retry-Num` 無視 + イベントID冪等化 + `notifications` PK |
| ローカル開発で受信 URL が要る | 開発速度 | 送信系は SLACK_* 未設定で no-op、受信系は録画ペイロードのユニットテストで代替（実受信は dev チャネル + 本番 URL で確認) |
| スレッド返信の拾いすぎ（bot 自身・無関係スレッド） | 誤対話 | `bot_id` 除外 + `thread_ts` が `notifications.message_ts` に一致するもののみ処理 |
| 訂正セッションの放置 | スレッドが宙に浮く | 30分で未確定クローズ（R3.5）をエスカレーションと同じ tick で処理 |
| プロンプト変更（category 追加）による RCA 回帰 | 推定品質低下 | 既存 `test_rca.py` にカテゴリ検証を追加。語彙外はサーバで other に正規化し通知は既定先へ |

## Sources

- [Slack Developer Docs — Changelog（classic apps 廃止・検証方式）](https://docs.slack.dev/changelog/)
- [Slack Events API（3秒ACK・再送仕様）](https://api.slack.com/events-api)
- [Bolt for Python — request verification middleware（署名検証の標準実装）](https://docs.slack.dev/tools/bolt-python/reference/middleware/index.html)
- 親スペック: [.kiro/specs/chokotei-anomaly-rca/design.md](../chokotei-anomaly-rca/design.md)
