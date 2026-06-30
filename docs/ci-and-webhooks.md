# DevOps CI/CD ＋ Discord 通知 — 最短セットアップ（「まわす」軸）

審査軸 **まわす** ＝ GitHub 連携・CI/CD・継続的改善。ここを最小構成で満たし、結果をチームに**可視化**するための参考資料です（`.claude/` には入れません）。

> 関連: `.claude/rules/gcp-integration.md`（de-risk Top5）／ `docs/strategy.md`（§2.1 配管ループ・§8）。

---

## 1. GitHub Actions → Cloud Run（キーレス WIF デプロイ）

`.github/workflows/deploy.yml` の最小形:

```yaml
name: deploy
on:
  push:
    branches: [main]
permissions:
  contents: read
  id-token: write          # ← WIF に必須（無いと 403）
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - id: auth
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.WIF_PROVIDER }}
          service_account: ${{ secrets.DEPLOY_SA }}      # ← 明示必須
      - uses: google-github-actions/setup-gcloud@v2
      - run: |
          gcloud run deploy "$SERVICE" \
            --source . --region us-central1 \
            --project "${{ secrets.GCP_PROJECT }}" --quiet
        env:
          SERVICE: hack-agent
```

### ⚠️ WIF の「4 大 403」（gcp-integration.md de-risk #3 と同一）
1. project は **番号**（ID ではない）
2. workflow に `id-token: write`
3. deploy に **明示 `service_account`**
4. SA へ `roles/iam.workloadIdentityUser` ＋ `roles/iam.serviceAccountTokenCreator`（IAM 反映に ~5分）

> WIF 配線は提出 repo 確定後に `GH_REPO=owner/repo bash scripts/setup-gcp.sh`（冪等）。

---

## 2. Discord 通知 Webhook（CI/デプロイ結果をチームへ）

「まわす」を**見える化**する。ビルド/デプロイ/テストの結果を team channel に流す。

**セットアップ**
1. Discord → 対象チャンネルの設定 → **連携 → Webhook を作成** → URL をコピー
2. GitHub repo の **Secrets** に `DISCORD_WEBHOOK_URL` として登録（**URL はコミットしない**）
3. workflow に通知ステップを追加:

```yaml
      - name: notify discord
        if: always()                    # 成功も失敗も流す
        run: |
          curl -fsS -H "Content-Type: application/json" -X POST \
            -d "{\"content\":\"deploy **${{ job.status }}** — \`${{ github.sha }}\`\"}" \
            "${{ secrets.DISCORD_WEBHOOK_URL }}"
```

**または** GitHub→Discord の標準連携: チャンネル設定 → 連携 → GitHub を選び、Webhook URL の**末尾に `/github`** を付けると push / PR が自動投稿されます（コード変更のフィードに最適）。

---

## 3. PR ゲート（任意・継続的改善の足場）

- **配管ループは自作しない**。`agent-starter-pack`（GA）の CI/CD（Cloud Build + GitHub Actions）＋ Vertex AI eval を採用すると、PR ごとの eval ゲート（Deploy-Hidden → Evaluate → Promote）が最短で乗る。
- これで「版ごとに数字が上がる」を**純正プリミティブ**で満たせる（差別化はドメイン側で、§strategy 2.1）。

---

## 注意（公開 repo 前提）
- **webhook URL / WIF provider / SA 名は Secrets**。`.env` やコードにベタ書きしない（`.claude/settings.json` の deny でも `.env`/SA キー読取は止めています）。
- 通知は**流しすぎない**。main の deploy 結果＋失敗時だけで十分（ノイズ化を避ける）。
- GitHub の **secret scanning** を有効化。誤って push したキーは即ローテ。
