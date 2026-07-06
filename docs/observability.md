# 可観測性（「まわす」の設計）

デプロイして終わりではなく、**推定品質と障害を運用中に観測できる**ことを P1 の範囲に含めている。

## 構造化ログ（Cloud Logging）

全サービスは起動時に `chokotei_shared.obs.setup_logging()` を通り、Cloud Run 上
（`K_SERVICE` 検出時）では **1行JSON** を stdout に出す。Cloud Logging は
`severity` をログレベル、`message` を本文として解釈し、残りのキーは
`jsonPayload` としてフィルタ可能になる。

```json
{"severity": "INFO", "message": "cold-start restore done", "component": "dashboard",
 "restored_events": 12, "rca_cache": 3}
{"severity": "ERROR", "message": "RCA failed", "component": "dashboard",
 "event_id": "ev-000003", "exception": "Traceback (most recent call last): ..."}
```

ローカル開発では従来どおりのプレーン形式（JSONを見たいときは `LOG_JSON=1`）。

### 見方（Logs Explorer / gcloud）

```bash
# RCA失敗だけを抽出（event_id 付きで原因調査できる）
gcloud logging read '
  resource.type="cloud_run_revision"
  resource.labels.service_name="chokotei-dashboard"
  severity>=ERROR' --project fhack26-aiclub --limit 20 --freshness 1d

# コールドスタート復元の実績（何件復元されたか）
gcloud logging read '
  resource.labels.service_name="chokotei-dashboard"
  jsonPayload.message="cold-start restore done"' --project fhack26-aiclub --limit 5
```

## メトリクス

| 何を | どこで | 意味 |
|---|---|---|
| 推定正答率 `correct_rate` | アプリ内 `/metrics`（HITLフィードバック集計） | **AIの品質**がHITLで改善しているか |
| リクエスト数 / レイテンシ / 5xx / インスタンス数 | Cloud Run 標準メトリクス（Cloud Monitoring） | サービスの健全性 |
| コールドスタート復元件数 | 構造化ログ `restored_events` | 状態継続性の確認 |

Cloud Run はデプロイだけで request_count / request_latencies / container/instance_count が
Cloud Monitoring に自動送信される（[サービス詳細 → 指標タブ]）。追加のエージェント導入は不要。

## 監査証跡（Req 10.4）

「AIが何を根拠に判断したか」を後から追える：

- `anomaly_events` — 何がいつ起きたか（決定論CVの検知結果）
- `rca_results.evidence` — エージェントが参照した数値・ログ
- `feedback` — 人間の裁定（correct / wrong + 訂正真因）
- `past_cases` — 訂正が次回推論に還流した記録（埋め込み付き）

## 既知の限界（正直に）

- 分散トレース（Cloud Trace）は未導入。単一サービス構成のため優先度を下げた。
- アラート（誤検知率の閾値通知など）は Cloud Monitoring のアラートポリシーで追加可能だが未設定。
- Gemini Vision 確認（境界帯）は失敗時 fail-open（安全側=異常扱い）で、失敗は WARNING ログに残る。
