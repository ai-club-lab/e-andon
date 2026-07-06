# e-Andon — AIアンドン：工場ライン見える化 × AI自律原因推定

[![ci](https://github.com/ai-club-lab/e-andon/actions/workflows/ci.yml/badge.svg)](https://github.com/ai-club-lab/e-andon/actions/workflows/ci.yml)

**稼働URL: https://chokotei-dashboard-523085315022.asia-northeast1.run.app**
（Cloud Run asia-northeast1。審査期間中は min-instances=1 で待機、以降 scale-to-zero）

生産ラインの「**チョコ停**」（部品の位置ずれ等で起きる微小な短時間停止）は、現場では
「止まったことは分かるが、**なぜ**かは人が調べる」もの。e-Andon は従来のアンドン（異常表示灯）を
AIエージェントで拡張し、**検知 → 停止 → 原因推定 → 人の裁定 → 次回の推論改善** を1本の閉ループにする。

![デモ: 稼働 → 整列異常検知 → チョコ停 → AIエージェントが原因を通知 → 人が裁定](docs/assets/demo.gif)

*↑ 実映像デモ: 部品が流れる → 整列ズレ検知（赤枠 offset 16px）→ ライン停止 → **AIエージェントが
「センサーは全て正常＝位置決め機構側の問題」と絞り込み、真因・確信度・根拠を通知** → 人が「正しい/違う」を裁定。*

## 何が痛いのか（インパクト試算）

チョコ停は TPM の設備「7大ロス」の一つ。1回は数分でも積み重なると大きく、
例えば計画稼働 480分/日のラインでチョコ停合計60分なら**それだけで稼働率 ▲12.5%**
（[チョコ停の損失計算とOEE](https://www.me-toyo.co.jp/news/chokotei/)）。
現場の実情は「復旧は数分、**原因調査は人が設備を見て回って数十分〜翌日持ち越し**」。
e-Andon はこの初動調査（どのセンサーを見るか・過去に同じことがなかったか）を、
停止通知と同時に**真因候補・確信度・根拠つきで数十秒に短縮**する。検知そのものの品質は
実映像241フレームで**誤検知0・異常1件を毎pushの回帰テストで担保**（[CI](.github/workflows/ci.yml)）。

## なぜ「AIエージェント」なのか（必然性）

```mermaid
flowchart LR
    subgraph DOM1["決定論領域：重い判断はLLMの外・監査ログ付き"]
        CV[OpenCV 決定論CV<br>整列ズレ/角度/ピッチ検知] -->|異常確定| STOP[ライン停止<br>チョコ停]
    end
    STOP --> AGENT
    subgraph DOM2["エージェント領域：なぜ？に答える"]
        AGENT[ADK エージェント<br>Gemini 2.5 Flash] -->|自律選択| T1[query_line_sensors<br>5チャネル照会]
        AGENT -->|自律選択| T2[query_logs<br>時間窓の統計]
        AGENT -->|自律選択| T3[search_past_cases<br>pgvector 類似検索]
    end
    AGENT -->|真因・確信度・根拠| HITL[人の裁定<br>正しい / 違う＋訂正]
    HITL -->|訂正を埋め込み化して蓄積| T3
```

- **停止判断は決定論**: 異常確定・ライン停止は OpenCV の幾何計算（閾値は PoC 実測: 正常σ≈1px vs 異常18px）。
  LLM に破壊的判断をさせない（ガードレール）。境界帯 (8–12px) だけ Gemini Vision が二段確認。
- **「なぜ」はエージェントでしか解けない**: 位置決め機構はセンサー非搭載＝ログを1本引けば済む問題ではない。
  エージェントが**自分でツールを選び**、5つの機械センサーを照会 →「全て正常」を**消去法の根拠**に変換 →
  過去事例をベクトル検索して真因を絞る。この探索・統合・推論の連鎖が Function Calling の自律ループ。
  通知にはエージェントが選んだ**ツール呼び出しの履歴**（例: `query_line_sensors → search_past_cases`）を
  根拠と併記する——自律性は主張でなく証跡で示す。チャットはセッション永続（Cloud SQL）で**マルチターン**。
- **使うほど賢くなる**: 人の訂正は Gemini Embedding で埋め込み → Cloud SQL (pgvector) に蓄積 →
  次回 RCA の few-shot に自動還流。正答率は `/metrics` で追跡。

### 学習ループの実測（Before / After）

`/feedback` と同一のコードパスで、**訂正1件が次回推論を変える**ことを実測した（Vertex 実呼び出し）:

| | AIの第一候補 |
|---|---|
| **Before** — 初見の横ズレ異常 | 「送り機構の速度低下による部品の位置ずれ」 |
| **HITL** — 現場が「違う」と裁定・真因を入力 | 「搬送ガイドレール固定ボルトの緩みによる横ズレ」 |
| **After** — 類似異常の再発時 | **「搬送ガイドレール固定ボルトの緩みによる横ズレ」**（訂正が第一候補に） |

訂正は埋め込み化されて `past_cases` に還流し、`rca_cache` の該当シグネチャを無効化するため、
同種の異常が再発した瞬間から推論が変わる。これが本作品の「まわす」= AIを継続的に改善するサイクル。

## アーキテクチャ

```mermaid
flowchart TB
    subgraph CloudRun["Cloud Run — asia-northeast1"]
        DET[detector<br>決定論CV + 二段確認] --> DASH[dashboard<br>FastAPI + SSE + HITL]
        AGENT[agent<br>ADK 2.3.0 オーケストレータ] --> DASH
    end
    DASH -->|SSE / chat| USER((オペレーター))
    AGENT -->|ADC・鍵不要| VERTEX[Vertex AI — us-central1<br>Gemini 2.5 Flash<br>gemini-embedding-001]
    DASH --> SQL[(Cloud SQL Postgres<br>+ pgvector<br>events / RCA / feedback /<br>past_cases / ADKセッション)]
    DASH --> GCS[(Cloud Storage<br>代表フレーム・非公開)]
    GH[GitHub Actions] -->|キーレス WIF| CloudRun
```

```
services/detector    決定論CV検知（整列/角度/ピッチ）＋ Gemini Vision 二段確認 ＋ 時系列集約
services/agent       ADK RCAエージェント（FunctionTools・DatabaseSessionService・埋め込み検索）
services/dashboard   FastAPI＋軽量フロント（稼働1ライン＋デモ表示3ライン・SSE・チャット・HITL・コールドスタート復元）
packages/shared      型付き契約(pydantic)・設定注入・構造化ログ(obs)
infra                Cloud SQL スキーマ・WIFセットアップ
.github/workflows    CI（テスト＋Dockerビルド）／CD（キーレスWIFデプロイ）
```

- **実行**: Cloud Run / **AI**: Vertex AI Gemini 2.5 Flash + gemini-embedding-001（ADC・鍵不要, us-central1）
- **状態**: Cloud SQL Postgres + pgvector（イベント・RCA・過去事例ベクトル・ADKセッション永続化）
- **エージェント**: google-adk==2.3.0（バージョン固定・セッション永続化 smoke test 済み）
- **なぜ Vertex AI Vector Search でなく pgvector か**: 過去事例はライン単位で高々数百〜数千件で、
  イベント・裁定・ADKセッションと**同じ Cloud SQL に同居**させると学習ループが1トランザクション空間で閉じ、
  追加の常駐コストもゼロ。この規模なら逐次スキャンの cosine で十分速く、埋め込みは
  gemini-embedding-001 を **768次元（MRL）**で使い pgvector の HNSW 2000次元制限とも無縁。
  事例が万単位に伸びたら Vector Search 2.0 へ移行する前提で、検索は `search_past_cases`
  ツール1箇所に隔離してある（差し替え点が1つ）。
- 仕様駆動: [.kiro/specs/chokotei-anomaly-rca/](.kiro/specs/chokotei-anomaly-rca/)（requirements → design → tasks を全トレース）

## つくる・まわす・とどける

- **つくる**: 上記。決定論CVとエージェントの役割分離が設計の核。
- **まわす** — CI/CD・可観測性:
  - **CI** ([ci.yml](.github/workflows/ci.yml)): 毎 push/PR で決定論CVテスト・**UIテスト（チャット/HITL裁定/メトリクス）**・コンパイル健全性・本番同一イメージの Docker ビルド。
  - **CD** ([deploy.yml](.github/workflows/deploy.yml)): **キーレス（Workload Identity Federation）**で `main` → Cloud Run 自動デプロイ。長期SAキーは発行しない。
    リードタイムは **main push → 本番反映 約3分**（直近の実測: 2m24s / 3m04s）。
  - **可観測性** ([docs/observability.md](docs/observability.md)): 構造化JSONログ（Cloud Logging で severity / event_id フィルタ可能）、
    HITL正答率 `/metrics`、Cloud Run 標準メトリクス。監査証跡（検知→根拠→裁定）はすべて Cloud SQL に残る。
- **とどける**: 上の稼働URLで誰でも動作確認可能。コールドスタート後も Cloud SQL から状態復元し、審査期間は min-instances=1 で待機なし。

## ローカル開発

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e packages/shared
pip install -r services/detector/requirements.txt   # 必要なサービスごと
# オフラインテスト（GCP認証不要・動画パスはリポジトリ直下基準）
PYTHONPATH=services/detector python -m pytest -q services/detector/test_detection.py services/detector/test_guardrails.py
# UIテスト（チャット / HITL裁定 / メトリクス — Vertexはモック）
PYTHONPATH=services/dashboard:services/agent:services/detector python -m pytest -q services/dashboard/test_server.py
```

## ガードレール（公開repo前提）

- APIキー/SAキー/`.env` は公開repoに入れない（**Secret Manager / ADC / WIF**）。
- 重い意思決定（異常確定・停止）は**決定論CV**に置き、LLMの外＋監査ログに（HITL）。詳細: [docs/audit.md](docs/audit.md)
- `gcloud projects delete` / `run services delete` は打たない（撤収は owner 判断）。

> 補足: Cloud Run サービス名・内部パッケージ名は旧称 `chokotei` のまま（稼働URL維持のための意図的判断）。
> プロダクト名・UIは e-Andon に統一している。
