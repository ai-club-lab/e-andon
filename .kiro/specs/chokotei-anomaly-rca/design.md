# Technical Design — chokotei-anomaly-rca

> ステータス: **design-generated** ／ 言語: ja ／ 承認要（Design 承認は Tasks の前提）
> 一次ソース: [requirements.md](requirements.md) / [research.md](research.md) / [../../../docs/poc/findings.md](../../../docs/poc/findings.md)
> 対応アーキ図: セッション内 `chokotei_anomaly_rca_architecture`

## 1. Overview & Design Goals

映像の整列異常を**決定論CV**で確定し、**ADKエージェント**がIoTログを相関して原因を推定、
チャットで通知、人が確認し訂正を蓄積して次回 few-shot に還す**閉ループ**。

設計目標:
- **1閉ループを稼働URLで成立**（採点要件）。scale-to-zero でコスト最小。
- **重い判定は決定論側**（異常確定はCV）、LLMは推論と説明に限定（ガードレール準拠）。
- **無音フォールバック禁止**・**セッション永続**・**監査ログ**を構造で担保。

## 2. Architecture Pattern & Boundary Map

**パターン**: イベント駆動の疎結合パイプライン ＋ HITL フィードバックループ。
P1 は Cloud Run 上の少数サービスで、境界は「関数/内部イベント」。P3 で Pub/Sub 実体化。

```
detector(worker) --anomaly_event--> orchestrator/agent --result--> dashboard(api+ui/chat)
        |                                   |                              |
        +--frames-->  Cloud Storage         +--tools--> Cloud SQL <--------+ (events/iot/feedback/rag)
                                            +--LLM-->  Vertex AI Gemini 2.5 Flash
```

サービス境界（P1）:
- **`detector`**（Cloud Run, 常駐 or job）: 映像→CV検知→時系列集約→異常イベント発行＋SSE配信。
- **`agent`**（Cloud Run）: ADK orchestrator。異常イベントを受け原因推定。ツールでCloud SQL/画像照会。
- **`dashboard`**（Cloud Run）: API＋UI＋チャット（SSE中継）＋HITL入力。P1では detector と同居も可。
- **共有**: `packages/shared`（pydantic スキーマ・DBモデル）。

> P1簡素化: `detector`＋`dashboard` を1サービス同居（SSEを内部共有）にし、`agent` を分離。→ Cloud Run 2本。

## 3. Technology Stack & Alignment

| 層 | 採用 | 根拠 |
|---|---|---|
| 言語 | Python 3.12（全サービス） | ADK/OpenCV/pydantic 一本化・速度 |
| 検知 | OpenCV（headless） | PoC実証・決定論・軽量 |
| Vision二段 | Vertex AI Gemini 2.5 Flash | ADC鍵不要・境界帯のみ |
| エージェント | **google-adk==2.0.x（厳密ピン）** | de-risk #2・AgentTool |
| セッション/DB | Cloud SQL Postgres + `DatabaseSessionService` | de-risk #1・`postgresql+asyncpg://` |
| RAG | pgvector | 次元でindex種別選択（#5） |
| 配信 | SSE（`sse-starlette`）/ FastAPI | 実装単純・双方向不要 |
| フロント | **未決**（FastAPI+軽量 or Next.js） | Design承認時に確定（§12） |
| 実行基盤 | Cloud Run（min-instances=0） | scale-to-zero |

## 4. Components & Interface Contracts

型は pydantic（境界で検証）。以降は契約であり実装ではない。

### 4.1 detector
```python
class PartObservation(BaseModel):
    cx: float; cy: float; angle: float          # 重心・回転角(deg)
class FrameResult(BaseModel):
    frame_index: int; ts: float
    baseline_y: float; median_gap: float; median_angle: float
    parts: list[PartObservation]
    flags: list["FlagDetail"]                    # offset/rotation/gap
class FlagDetail(BaseModel):
    kind: Literal["offset","rotation","gap"]
    cx: float; cy: float; magnitude: float; reason: str
```
- `detect_frame(frame) -> FrameResult`：R2。閾値は設定注入（offset=10px, angle=10deg, gap_ratio=1.5, band=8–12px）。
- `confirm_with_gemini(crop) -> bool`：R2.5。境界帯のみ呼び出し。
- `stream() -> Iterator[AnnotatedFrame]`：R1。5fps・ループ・オーバレイ・SSE。

### 4.2 event aggregator（detector内）
```python
class AnomalyEvent(BaseModel):
    event_id: str; started_ts: float; ended_ts: float | None
    kind: Literal["offset","rotation","gap"]
    peak_magnitude: float; rep_frame_uri: str    # Cloud Storage 参照
    status: Literal["open","closed"]
```
- `update(frame_result) -> AnomalyEvent | None`：R3。同一変位部品を追跡し1イベント化。
  新規確定時のみ下流トリガー（R3.3）。閾値未満が一定継続で `closed`（R3.4）。

### 4.3 agent（ADK orchestrator）
- root(orchestrator) ＋ tools（**AgentTool/FunctionTool**、de-risk #2）:
  - `query_vibration(event_id, window_s) -> list[IoTReading]`（R4.2, R5.2）
  - `query_logs(channel, t0, t1) -> list[IoTReading]`（R6.2）
  - `search_past_cases(query) -> list[FeedbackCase]`（R5.4, R9.1 / pgvector）
  - `get_frame(event_id) -> ImageRef`（R5.2）
```python
class RcaResult(BaseModel):
    event_id: str
    cause_candidates: list[str]                  # 原因候補（順位付き）
    confidence: float                            # 0..1
    evidence: list[str]                          # 参照した数値/ログ（根拠）
```
- `infer(event: AnomalyEvent) -> RcaResult`：R5。セッションは `DatabaseSessionService`（R5.5）。
  失敗時は例外送出＋記録（**無音フォールバック禁止** R5.6）。

### 4.4 dashboard / chat
- `GET /stream`（SSE 映像＋オーバレイ, R1/R7）
- `GET /events`（異常一覧, R7.3）／ `GET /iot?channel&t0&t1`（R6.2/R7）
- `POST /chat`（通知面＋対話照会, R6）→ 内部で agent tools を叩く
- `POST /feedback`（HITL, R8）
```python
class Feedback(BaseModel):
    event_id: str; ai_result: RcaResult
    verdict: Literal["correct","wrong"]
    human_cause: str | None; ts: float           # wrong時に human_cause 必須
```

### 4.5 iot synthesizer
- `generate(timeline) -> None`：R4.1/4.3。映像tに整合。異常窓にX軸加速度スパイク＋高調波を注入。
  チャネル: `vibration_x/y/z`, `temperature`, `motor_current`。

## 5. Data Model（Cloud SQL Postgres）

```
anomaly_events(event_id PK, started_ts, ended_ts, kind, peak_magnitude, rep_frame_uri, status, created_at)
iot_readings(id PK, ts, channel, value)                        -- index(ts, channel)
rca_results(event_id FK, cause_candidates jsonb, confidence, evidence jsonb, created_at)
feedback(id PK, event_id FK, verdict, ai_cause, human_cause, created_at)  -- R8.3
past_cases(id PK, embedding vector, summary, correct_cause, source_event_id)  -- RAG, R9
-- ADK 管理: sessions / events（DatabaseSessionService が生成、asyncpg）
```
- 監査ログ: `anomaly_events`＋`rca_results.evidence` で判定根拠を保全（R2.7, R10.4）。

## 6. Key Sequences

**検知→通知（正常系）** R1–R6:
1. detector が5fpsで `detect_frame` → `FrameResult`（オーバレイをSSE配信）
2. aggregator が新規 `AnomalyEvent` 確定 → 代表フレームを Cloud Storage 保存
3. agent.`infer` 起動 → `query_vibration` で相関窓抽出 → Gemini推論 → `RcaResult`
4. dashboard がチャットへ通知（原因候補・確信度・根拠）

**HITL→学習** R8–R9:
1. ユーザーが `/feedback` で正誤＋（誤りなら）正しい原因を送信
2. `feedback` 保存。`wrong` は `past_cases` に埋め込み登録
3. 次回 `infer` の `search_past_cases` が訂正済み事例を優先 few-shot 化

## 7. Detection Design（PoC継承）

- ROI(y=250..430)→Otsu→モルフォロジ→輪郭→`minAreaRect`（重心/角度）。面積・アスペクトでフィルタ。
- 3シグナル: `offset`(主) / `rotation` / `gap`(融合時バックアップ)。中央値基準で相対判定（照明変動に頑健）。
- 時系列集約で1イベント化（§4.2）。閾値・ROIは設定ファイル注入（現場調整可）。

## 8. Agent Design（ADK 2.0）

- `google-adk==2.0.x` 厳密ピン。`SESSION_DB_URL=postgresql+asyncpg://...`。events新カラム込みでschema初期化。
- root orchestrator＋FunctionTool群（§4.3）。`transfer_to_agent` 不使用。
- Gemini: ツールスキーマの `anyOf/default` サニタイズ、`HttpRetryOptions[429]` 明示ON、us-central1。
- 失敗は例外＋構造化ログ（R5.6/R10.5）。

## 9. Deployment & GCP Mapping

- Cloud Run 2本（`agent` ／ `dashboard+detector` 同居）、min-instances=0。
- Cloud SQL Postgres（pgvector）: `--add-cloudsql-instances`＋SAに `roles/cloudsql.client`（#5）。
- Cloud Storage（private）: 異常フレーム。Vertex AI: ADC。Logging/Monitoring/Trace: 可観測性。
- P1デプロイは**ローカル `gcloud run deploy --source .`**（WIFは提出repo確定後Mao、#3）。

## 10. Requirements Traceability

| Req | 充足コンポーネント |
|---|---|
| 1.x 映像/配信 | detector.stream / dashboard GET /stream |
| 2.x 検知二段 | detector.detect_frame / confirm_with_gemini |
| 3.x イベント集約 | event aggregator |
| 4.x IoT | iot synthesizer / query_vibration / iot_readings |
| 5.x 推定 | agent.infer / tools / DatabaseSessionService |
| 6.x チャット | dashboard POST /chat / query_logs |
| 7.x 監視 | dashboard UI / GET /events / GET /iot |
| 8.x HITL | dashboard POST /feedback / feedback表 |
| 9.x 学習 | search_past_cases / past_cases / 指標表示 |
| 10.x 非機能 | Cloud Run/SQL/ADC/監査/リージョン規定 |

## 11. Risks & Mitigations

research.md §Risks（de-risk Top5）を継承。追加:
- **時系列トラッキングの誤結合**（別部品を同一視）: cx連続性＋速度で追跡、途切れで新IDを慎重に。
- **境界帯Gemini呼びの多発**: 帯を狭く（8–12px）・イベント単位で1回に制限。

## 12. Open Decisions（Design承認時に確定）

1. **フロント技術**: FastAPI+軽量フロント（Python一本・速い）↔ Next.js（見栄え）。
2. **Pub/Sub をP1に入れるか**（既定: 入れず関数境界、P3で実体化）。
3. **製品名**。
4. **RAG 埋め込み次元 → pgvector index 種別**（次元確認後 HNSW/IVFFlat）。
