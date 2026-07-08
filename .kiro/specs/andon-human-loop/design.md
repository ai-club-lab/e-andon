# Technical Design — andon-human-loop

> ステータス: **design-generated** ／ 言語: ja ／ 承認要（Design 承認は Tasks の前提）
> 一次ソース: [requirements.md](requirements.md) / [research.md](research.md) / 親 [chokotei-anomaly-rca/design.md](../chokotei-anomaly-rca/design.md)

## 1. Overview & Design Goals

AI ループ（検知→推定→裁定→学習）の外側に**人間側ループ**（通知→駆けつけ→修理→確認）を追加する。
プッシュ通知（Slack）・責任者ルーティング・エスカレーション・分析ビュー・モバイル裁定動線・写真還流。

設計目標:
- **既存コードパスへの合流**: 裁定・訂正・学習還流は既存の単一パス（`/feedback`・`/correct`・
  `_persist_correction`）を共有し、Slack は「入口と出口が増えるだけ」にする（二重実装ゼロ）。
- **決定論ガードレールの維持**: 通知先解決・エスカレーション発火・書き込み先固定は LLM の外。
  LLM の寄与は「閉じた語彙からのカテゴリ選択」まで。
- **Slack なしで全機能・全テストが成立**（ローカル・CI）。シンク未設定は no-op、着信はフィクスチャ検証。

## 2. Architecture Pattern & Boundary Map

**パターン**: 既存のステートフル・シングルトン（dashboard 1コンテナ）に、
**Outbound 抽象（NotificationSink）** と **Inbound アダプタ（/slack/*）** を追加するポート&アダプタ拡張。
新サービスは増やさない。

```
                       ┌──────────────── dashboard (Cloud Run, singleton) ───────────────┐
 detector/tracker ──▶  stop event ──▶ notifier ──▶ NotificationSink ──▶ Slack chat.postMessage
                                        │   │                              (card + mention)
                                        │   └─▶ SSE queue（既存・変更なし）
                                        ▼
                                   routing.resolve(category)  ◀── routing_rules (Cloud SQL)
                                        │
                                   escalation scheduler（10s tick, asyncio）◀── escalations (Cloud SQL)
                                        │ 無応答5分/15分
                                        └─▶ NotificationSink（第2段/第3段通知）

 Slack ボタン/スレッド返信 ──▶ POST /slack/interactivity | /slack/events（署名検証・即ACK）
                                        │ block_actions: verdict        │ thread message
                                        ▼                               ▼
                              _record_verdict（共有・既存 /feedback と同一）   elicit_correction（既存）
                                        │                               │
                                        └────── feedback / past_cases / rca_cache 無効化（既存パス）

 スマホ（Slack カードの deep link）──▶ GET /e/{event_id}（モバイル裁定・訂正ページ）
 管理者 ──▶ GET /analytics（パレート・再発・正答率 = /analytics/* JSON + SVG 描画）
```

境界の要点:
- **`notifier`**（新規モジュール）: 停止イベント+RCA+ルーティング決定を受けてカードを構成。
  出口は `NotificationSink` Protocol のみに依存。
- **`/slack/*`**（新規ルート）: 署名検証 → 即 200 → `asyncio.create_task` で本処理。
  本処理は既存の裁定・訂正関数を呼ぶだけ（Slack 固有の解釈はこの層で完結）。
- **escalation scheduler**: 唯一の新規常駐処理。決定論（時刻×裁定状態）・DB 永続・起動時復元。

## 3. Technology Stack & Alignment

| 層 | 採用 | 根拠 |
|---|---|---|
| Slack 送信 | `slack_sdk` WebClient（`chat.postMessage` / `chat.update`） | 公式 SDK・薄い |
| Slack 受信 | FastAPI ルート + `slack_sdk` SignatureVerifier（HTTP モード） | research R-2。Bolt/Socket Mode 不採用 |
| カード表現 | Block Kit（section + image + actions） | 裁定ボタン・メンション・deep link を1カードに |
| 秘密 | `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` を Secret Manager → env 注入 | R10.3・既存 deploy.yml パターン |
| ルーティング | Cloud SQL `routing_rules` テーブル | 再デプロイ不要更新（R5.5）・監査に版を記録 |
| タイマー | asyncio 周期タスク + `escalations` テーブル | research R-3・シングルトン整合（R10.2） |
| 分析描画 | サーバ集計（SQL）→ JSON → 素 JS インライン SVG | 依存ゼロ・既存フロント整合（research R-6） |
| 写真保存 | GCS（既存 `frames_store` と同パターン、非公開+プロキシ） | R9.6 |
| マルチモーダル | 検索 top-1 ヒットの写真を Gemini 入力 Part に追加 | research R-5 |

既存スタック（FastAPI / ADK 2.3.0 / Cloud SQL + pgvector / 素 JS）は変更しない。

## 4. Components & Interface Contracts

型は pydantic（境界で検証）。以降は契約であり実装ではない。

### 4.1 contracts 追加（`packages/shared/chokotei_shared/contracts.py`）

```python
CauseCategory = Literal["positioning", "conveyance", "sensor", "other"]

class RcaResult(BaseModel):            # 既存に1フィールド追加（後方互換: default="other"）
    ...
    category: CauseCategory = "other"  # サーバ側で語彙検証・正規化（R5.1, R5.4）

class Actor(BaseModel):                # 裁定・訂正の操作者（R4）
    surface: Literal["dashboard", "slack"]
    user_id: str                       # Slack user ID or dashboard user_id
    display_name: str | None = None

class RoutingDecision(BaseModel):      # 監査に残す決定の全材料（R5.6）
    event_id: str
    category: CauseCategory
    rule_version: int                  # routing_rules の版
    primary_mention: str               # 例 "<@U123>" / "<!subteam^S123>"
    escalation_plan: list["EscalationStep"]

class EscalationStep(BaseModel):
    tier: Literal[2, 3]
    delay_s: int                       # 既定: tier2=300, tier3=900（累積でなく前段からの間隔）
    target_mention: str | None         # tier3 は None（ベンダー連絡先の提示のみ, R6.3）
    contact_note: str | None = None

class NotificationRecord(BaseModel):   # 冪等化の要（R1.5）
    event_id: str                      # PK — 高々1カード
    channel_id: str
    message_ts: str                    # Slack thread の相関キー
    posted_at: float
```

### 4.2 NotificationSink（`services/dashboard/sinks.py` 新規）

```python
class NotificationSink(Protocol):
    def enabled(self) -> bool: ...
    async def post_card(self, ev: AnomalyEvent, rca: RcaResult,
                        routing: RoutingDecision, deep_link: str) -> NotificationRecord | None: ...
    async def update_card(self, rec: NotificationRecord,
                          verdict: str, actor: Actor) -> None: ...          # R2.5
    async def post_thread(self, rec: NotificationRecord, text: str) -> None: ...  # 訂正対話・エスカレーション

class SlackSink:   # 最初の実装（R1.1）。token 未設定 → enabled()=False で全メソッド no-op
    ...
class NullSink:    # ローカル/CI（R10.6）
    ...
```

- `post_card` 失敗: 例外を握りつぶさず構造化ログ + `state.sink_error` に記録し
  ダッシュボードのバナーで明示（R1.4。既存「無音フォールバック禁止」原則）。
- 冪等化: 投稿前に `notifications` テーブルを PK 参照。存在すれば再投稿しない（R1.5）。

### 4.3 routing（`services/dashboard/routing.py` 新規）

```python
def resolve(event_id: str, category: CauseCategory) -> RoutingDecision:
    """routing_rules テーブルの JOIN のみで通知先を解決する（LLM 不関与, R5.2/R5.6）。
    未登録 category → 既定ルール（班長）+ 未登録発生を構造化ログへ（R5.4）。"""
```

- `routing_rules`（Cloud SQL）: `(category PK, primary_mention, tier2_mention, tier2_delay_s,
  tier3_contact, tier3_delay_s, version, updated_at)`。シードは schema.sql、更新は SQL/管理手順で
  再デプロイ不要（R5.5）。
- RCA 側: `_INSTRUCTION` の JSON 出力に `"category"` を追加（enum 4値を明示）。
  `infer()` がサーバ側で語彙検証し不正値は `"other"` に正規化（R5.1, R5.4 — 決定論の砦はサーバ）。

### 4.4 escalation scheduler（`services/dashboard/escalation.py` 新規）

```python
class EscalationEngine:
    """10秒 tick。fire_at <= now かつイベント未裁定の行を発火（決定論, R6.6）。"""
    async def schedule(self, decision: RoutingDecision) -> None      # 通知成功時に tier2/3 を登録
    async def cancel(self, event_id: str) -> None                    # 裁定・応答で以降を停止（R6.4）
    async def restore(self) -> None                                  # 起動時に未来分を DB から復元（R10.2）
```

- `escalations`（Cloud SQL）: `(id, event_id, tier, fire_at, target_mention, contact_note,
  state: pending|fired|cancelled, fired_at)`。発火・取消は全て監査記録（R6.5）。
- 「応答」の定義（R6.2/R6.4）: 裁定ボタン押下 or 当該スレッドへの人間の返信 or `/e/{id}` での裁定。
- 訂正対話の 30 分クローズ（R3.5）も同じ tick で処理（`correction_sessions` の最終更新時刻を見る）。

### 4.5 Slack 受信（`services/dashboard/slack_routes.py` 新規）

```python
POST /slack/interactivity   # block_actions: verdict_correct / verdict_wrong
POST /slack/events          # url_verification / message.channels（スレッド返信・画像）
```

- 全リクエスト: SignatureVerifier（timestamp 5分窓）→ 検証失敗は 401 + 構造化ログ（R10.4）。
- **3秒 ACK**: 即 200 を返し `asyncio.create_task` で本処理（Slack 再送は `X-Slack-Retry-Num` を
  ACK のみで無視。冪等性は verdict 側の「裁定済みなら二重記録しない」= R2.4 が最終防衛）。
- スレッド返信の対応付け: `thread_ts == notifications.message_ts` かつ `bot_id` なしのみ処理。
  `elicit_correction(ctx, text, user_id=slack_user_id)` に流し、応答を `post_thread` で返す（R3.1–R3.4）。
- 画像付き返信: `files:read` で `url_private` を取得 → `attachments_store.save(event_id, bytes)`
  （GCS 非公開）→ 訂正確定時に `past_cases.attachment_uri` へ紐づけ（R9.1–R9.2）。
- 必要スコープ: `chat:write`, `files:read`, `users:read`。Event 購読: `message.channels`。

### 4.6 裁定の単一パス化（`server.py` リファクタ）

```python
def _record_verdict(event_id: str, verdict: str, actor: Actor,
                    human_cause: str = "") -> dict:
    """既存 /feedback の本体を抽出。dashboard・Slack・/e/{id} の3入口が合流する唯一の書き込み点。
    裁定済みイベントへの再操作は二重記録せず既裁定情報を返す（R2.4）。"""
```

- `feedback` テーブルに `actor_surface / actor_id / actor_name` カラム追加（R4.1–R4.2）。
- 裁定成立時: `EscalationEngine.cancel` → `SlackSink.update_card`（R2.5, R6.4）。
- 専用ログイン機構は導入しない（R4.3）。dashboard 面は従来どおり `user_id`（既定 "line-op"）。

### 4.7 分析ビュー（`analytics.py` 新規 + `static/analytics.html`）

```python
GET /analytics/pareto?days=7    -> {buckets: [{category, count, loss_minutes, cum_ratio}], total}
GET /analytics/accuracy?days=30 -> {points: [{date, correct_rate, n}]}
GET /analytics/recurrence?days=7 -> {alerts: [{category, count, threshold, suggestion}]}
```

- 集計はサーバ側 SQL（`anomaly_events ⋈ rca_results ⋈ feedback`）。訂正済みイベントは
  訂正後カテゴリを優先。損失分 = `ended_ts - started_ts`（open は既定停止時間でクリップ）。
- 再発検知（R7.4）: 窓 7 日 × 同一 category ≥ 3 件で alert。`suggestion` は定型文＋
  当該事例の直近訂正原因の引用（LLM 生成はオプション・キャッシュ前提。閾値判定自体は決定論）。
- データなし期間は `{buckets: [], empty: true}` を返し UI が「データなし」を明示（R7.7）。
- グラフ→イベント一覧へのドリルダウンは既存 `/events` を category/期間フィルタ付きに拡張（R7.6）。
- サイドメニューの「準備中」項目を本ビューへの遷移に置換（R7.1）。

### 4.8 モバイル裁定ページ（`GET /e/{event_id}` + `static/event.html`）

- カードの deep link 先。1カラム・390px 基準・横スクロールなし（R8.1）。
- 1画面目: 真因候補・確信度・根拠・代表フレーム（`/frame/{id}`）＋ 裁定2ボタン（≥44px, R8.2–R8.3）。
- 「違う」→ 同ページ内で訂正チャット（既存 `/correct` API）＋ 写真添付 `<input type=file>`
  → `POST /correct/attachment`（image/* のみ・10MB 上限, R9.5）。
- 既存ダッシュボード（俯瞰）は対象外（R8.4）。

### 4.9 写真のマルチモーダル還流（`past_cases.py` / `rca_agent.py` 拡張）

- `past_cases.attachment_uri TEXT` カラム追加（起動時自己マイグレーション — 既存 `ensure_schema` 拡張）。
- `search_past_cases` の返却に `attachment_uri` を含め、`infer()` は **top-1 に写真があるときのみ**
  GCS から取得して `types.Part`（画像）としてプロンプトに追加（R9.3。コンテキスト膨張抑制）。
- 添付なし訂正は従来どおり完結（R9.4）。

## 5. Data Model（追加・変更）

```sql
-- 追加
CREATE TABLE notifications (
  event_id   TEXT PRIMARY KEY REFERENCES anomaly_events(event_id),
  channel_id TEXT NOT NULL,
  message_ts TEXT NOT NULL,
  posted_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE routing_rules (
  category        TEXT PRIMARY KEY,           -- positioning/conveyance/sensor/other
  primary_mention TEXT NOT NULL,
  tier2_mention   TEXT NOT NULL,
  tier2_delay_s   INT  NOT NULL DEFAULT 300,
  tier3_contact   TEXT NOT NULL,
  tier3_delay_s   INT  NOT NULL DEFAULT 900,
  version         INT  NOT NULL DEFAULT 1,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE escalations (
  id         BIGSERIAL PRIMARY KEY,
  event_id   TEXT NOT NULL REFERENCES anomaly_events(event_id),
  tier       INT  NOT NULL,
  fire_at    TIMESTAMPTZ NOT NULL,
  target_mention TEXT,
  contact_note   TEXT,
  state      TEXT NOT NULL DEFAULT 'pending', -- pending|fired|cancelled
  fired_at   TIMESTAMPTZ
);
-- 変更（起動時自己マイグレーション: 既存 ensure_schema パターン）
ALTER TABLE rca_results ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'other';
ALTER TABLE feedback    ADD COLUMN IF NOT EXISTS actor_surface TEXT,
                        ADD COLUMN IF NOT EXISTS actor_id TEXT,
                        ADD COLUMN IF NOT EXISTS actor_name TEXT;
ALTER TABLE past_cases  ADD COLUMN IF NOT EXISTS attachment_uri TEXT;
```

## 6. Error Handling & Observability

- Slack 送信失敗: リトライ（`slack_sdk` の rate-limit 尊重）→ 最終失敗は
  `severity=ERROR` 構造化ログ（event_id 付き）＋ダッシュボードバナー（R1.4）。SSE 通知は常に先に出す。
- 署名検証失敗: 401 + `WARNING` ログ（R10.4）。ペイロードは記録しない（トークン類の混入防止）。
- すべての新規処理（通知・裁定・エスカレーション・添付）は `chokotei_shared.obs` の
  構造化ログ（event_id / severity フィルタ可能）に出力（R10.5）。
- 監査の一貫性: 「検知→根拠→通知→ルーティング決定→エスカレーション→裁定（actor）→訂正」が
  すべて Cloud SQL 上で event_id で連結される。

## 7. Testing Strategy

| 対象 | 方式 |
|---|---|
| Slack 署名検証・ACK・ボタン→裁定 | 録画ペイロード（フィクスチャ）+ SignatureVerifier 実計算。Slack 実呼び出しなし（R10.6） |
| 裁定の単一真実・二重裁定防止 | `test_server.py` 拡張（dashboard 経由と `/slack/interactivity` 経由で同一レコード） |
| ルーティング・エスカレーション | 決定論なので純ユニット（fake clock で 5分/15分/取消/復元） |
| RCA category | `test_rca.py` に語彙検証（enum 外→other 正規化）追加 |
| 分析 SQL | ローカル JSONL/SQLite 相当のフォールバックでなく、集計関数へ直接フィクスチャ注入 |
| モバイルページ | 既存 UI テストと同型（HTML 取得 + API 契約）。実機幅は手動確認 |

## 8. Requirements Traceability

| Req | 実現箇所 |
|---|---|
| 1.1–1.5 | §4.2 NotificationSink / SlackSink / notifications PK / エラーバナー |
| 2.1–2.5 | §4.5 interactivity + §4.6 `_record_verdict` + `update_card` |
| 3.1–3.5 | §4.5 スレッド対応付け + 既存 `elicit_correction` + §4.4 30分クローズ |
| 4.1–4.4 | §4.6 actor カラム + 監査ログ（ログイン機構なし） |
| 5.1–5.6 | §4.3 routing + RCA category（enum 正規化）+ routing_rules 版記録 |
| 6.1–6.6 | §4.4 EscalationEngine（tick・2段・取消・復元・監査・決定論） |
| 7.1–7.7 | §4.7 analytics + サイドメニュー置換 + ドリルダウン |
| 8.1–8.4 | §4.8 `/e/{event_id}` モバイルページ |
| 9.1–9.6 | §4.5 画像受信 + §4.8 添付 + §4.9 attachment_uri / top-1 還流 / 非公開プロキシ |
| 10.1–10.6 | §2 決定論境界 / §4.4 復元 / Secret Manager / §4.5 署名検証 / §6 ログ / NullSink+フィクスチャ |

## 9. Rejected Alternatives

- **Socket Mode / Bolt for Python / Cloud Tasks** — [research.md](research.md) R-2, R-3 参照。
- **通知を Pub/Sub 経由に** — シンク1実装・シングルトンの現在、間接層の価値なし。
  複数シンク+複数ラインで再検討（分割線は `NotificationSink` に既にある）。
- **裁定専用の Slack モーダル（views.open）** — ボタン2択+スレッド対話で足りる。
  モーダルは trigger_id の 3 秒制約と状態管理を増やす割に体験が向上しない。

## 10. 未決事項の解消（requirements「未決事項」への回答）

| 未決事項 | 決定 |
|---|---|
| Slack 受信方式 | HTTP（Events API + Interactivity）+ 署名検証（research R-2） |
| エスカレーションタイマー | プロセス内 asyncio + DB 復元（research R-3） |
| 真因カテゴリの語彙 | RCA 出力に enum 4値を追加・サーバ正規化（research R-4） |
| 分析ビュー描画 | サーバ集計 + インライン SVG（research R-6） |
| 写真還流の頻度 | 検索 top-1 のみ（research R-5） |
