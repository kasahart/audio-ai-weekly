# GitHub Copilot への引き継ぎ書
## Audio AI Weekly / 音響AI週報

**作成日:** 2026年4月25日　　**引き継ぎ元:** Claude Sonnet 4.6　　**引き継ぎ先:** GitHub Copilot

---

| ✅ 完了済み | ☐ 残タスク |
|---|---|
| 設計・実装・コミット | GitHub 設定・テスト・デバッグ |

---

## 1. プロジェクト概要

| 項目 | 内容 |
|---|---|
| プロジェクト名 | Audio AI Weekly / 音響AI週報 |
| 対象分野 | 音の基盤モデル・音源分離・異音検知 |
| 更新頻度 | 毎週金曜日 21:00 JST（GitHub Actions cron） |
| AI 解析エンジン | GitHub Models（Claude）／ `GITHUB_TOKEN` 認証 |
| フロントエンド | React 18 + Vite → GitHub Pages で配信 |
| データ管理 | 週次 JSON（`YYYY-MMDD.json`）＋ `index.json` で全週保持 |
| 設計書 | 要件定義書 v1.3（`system_design.md`） |

---

## 2. Claude が完了した作業

> リポジトリへのコミットまで完了しています。

### 2.1 ドキュメント
- ✅ 要件定義書 v1.3（設計変更 4 件 ＋ DevContainer 章を含む）
- ✅ アーキテクチャ設計書（システム構成・データフロー・スキーマ定義）
- ✅ `README.md`（セットアップ手順・コマンド一覧）

### 2.2 設定ファイル
- ✅ `config/keywords.yaml` — フィルタリングキーワード（22 件、追加・削除可能）
- ✅ `config/settings.yaml` — `max_papers=50`、GitHub Models エンドポイント等

### 2.3 バックエンドスクリプト（Python 3.11）
- ✅ `scripts/fetch_papers.py` — arXiv API 取得・キーワードフィルタ・カテゴリ分類
- ✅ `scripts/analyze_papers.py` — GitHub Models (Claude) で 6 観点日本語解析
- ✅ `scripts/build_data.py` — 週次 JSON 生成・`index.json` 更新
- ✅ `scripts/test_connection.py` — GitHub Models 疎通確認ツール
- ✅ `requirements.txt` — `openai`, `pyyaml`

### 2.4 CI/CD
- ✅ `.github/workflows/update.yml` — 4 ジョブ（fetch → analyze → build → deploy）

### 2.5 フロントエンド（React 18 + Vite）
- ✅ `web/src/App.jsx` — データ取得・週セレクター・カテゴリフィルター状態管理
- ✅ `web/src/components/Header.jsx`
- ✅ `web/src/components/WeekSelector.jsx` — `index.json` から週一覧を生成
- ✅ `web/src/components/CategoryFilter.jsx`
- ✅ `web/src/components/PaperCard.jsx` — ヘッダークリックで 6 観点を一括展開
- ✅ `web/src/components/TrendSummary.jsx`
- ✅ `web/index.html`・`vite.config.js`・`package.json`

### 2.6 DevContainer
- ✅ `.devcontainer/devcontainer.json`
- ✅ `devcontainer features` で Node 20・GitHub CLI・common-utils を導入
- ✅ `devcontainer.json` に venv・npm・gh auth 状態確認を集約

---

## 3. GitHub Copilot への残タスク

### 3.1 GitHub リポジトリ設定【最優先】

**☐ GitHub Pages を有効化する**
```
Settings → Pages → Source: Deploy from branch
→ Branch: gh-pages / (root) → Save
```

**☐ Actions の書き込み権限を確認する**
```
Settings → Actions → General
→ Workflow permissions → Read and write permissions → Save
```

> `GITHUB_TOKEN` は Actions が自動発行するため、Secrets への手動登録は**不要**です。

---

### 3.2 初回動作確認

**☐ DevContainer を起動し疎通確認スクリプトを実行する**
```bash
python scripts/test_connection.py
```

**☐ arXiv 取得のドライランを実行する**
```bash
python scripts/fetch_papers.py --dry-run
```

**☐ Actions タブから手動実行（workflow_dispatch）し、全ジョブが green になることを確認**
```
Actions → Weekly Audio AI Update → Run workflow → Run workflow
```

**☐ GitHub Pages の URL でフロントエンドが表示されることを確認する**
```
https://YOUR_ORG.github.io/audio-ai-weekly/
```

---

### 3.3 デバッグ観点

| 症状 | 原因 | 対処 |
|---|---|---|
| analyze ジョブが失敗 | GitHub Models のレート制限 | `settings.yaml` の `retry_interval` を増やす（例: `10.0`） |
| deploy ジョブが失敗 | GitHub Pages が未有効 | 3.1 の Pages 設定を実施する |
| フロントに論文が表示されない | `data/` が `web/public/data/` にコピーされていない | `update.yml` の「Copy data to web/public」ステップを確認 |
| カテゴリ分類が「その他」になる | `keywords.yaml` のキーワードが論文に一致しない | `include` キーワードを追加・調整する |
| 週次 JSON がスキップされる | 同一日付のファイルが既に存在する | 正常動作。再実行する場合は `data/weekly/YYYY-MMDD.json` を削除してから実行 |
| GitHub Models の認証エラー | `GITHUB_TOKEN` の権限不足 | `permissions: contents: write` が `update.yml` に設定されているか確認 |

---

### 3.4 今後の拡張候補

- ☐ 論文の被引用数・GitHub スター数の表示（Semantic Scholar API 連携）
- ☐ Slack / LINE への週次通知（GitHub Actions から webhook）
- ☐ キーワードヒット数の可視化（週次トレンドグラフ）
- ☐ 論文 PDF サマリーの追加（arXiv HTML 版からの本文取得）

---

## 4. ファイル構成と状態

| パス | 説明 | 状態 |
|---|---|---|
| `.devcontainer/devcontainer.json` | Python 3.11 image + features + 初回セットアップ | ✅ 実装済 |
| `.github/workflows/update.yml` | 4 ジョブの自動実行ワークフロー | ✅ 実装済 |
| `config/keywords.yaml` | フィルタキーワード（追加・削除はここだけ） | ✅ 実装済 |
| `config/settings.yaml` | `max_papers=50` 等のシステム設定 | ✅ 実装済 |
| `data/index.json` | 全週インデックス（初期は空） | ✅ 実装済 |
| `data/weekly/YYYY-MMDD.json` | 週次論文データ | ☐ Actions 実行後に自動生成 |
| `scripts/fetch_papers.py` | arXiv 取得・フィルタ・カテゴリ分類 | ✅ 実装済 |
| `scripts/analyze_papers.py` | GitHub Models で 6 観点解析 | ✅ 実装済 |
| `scripts/build_data.py` | 週次 JSON 生成・index 更新 | ✅ 実装済 |
| `scripts/test_connection.py` | GitHub Models 疎通確認 | ✅ 実装済 |
| `requirements.txt` | `openai`, `pyyaml` | ✅ 実装済 |
| `web/src/App.jsx` | メインコンポーネント・状態管理 | ✅ 実装済 |
| `web/src/components/PaperCard.jsx` | 論文カード（6 観点アコーディオン） | ✅ 実装済 |
| `web/src/components/WeekSelector.jsx` | 週セレクタードロップダウン | ✅ 実装済 |
| `web/package.json` / `vite.config.js` | Vite ビルド設定 | ✅ 実装済 |
| `README.md` | セットアップ・操作手順 | ✅ 実装済 |

---

## 5. データフローの概要

```
毎週金曜 21:00 JST
        │
        ▼
┌─────────────┐
│    fetch    │  fetch_papers.py
│  arXiv API  │  → data/raw_papers.json
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   analyze   │  analyze_papers.py
│ GitHub      │  GITHUB_TOKEN で認証
│ Models      │  → data/analyzed_papers.json
└──────┬──────┘
       │
       ▼
┌─────────────┐
│    build    │  build_data.py
│ 週次 JSON   │  → data/weekly/YYYY-MMDD.json
│ index 更新  │  → data/index.json
│ git push    │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   deploy    │  vite build
│ GitHub Pages│  → gh-pages ブランチ
└─────────────┘
```

> ⚠️ **レート制限に注意：** GitHub Models Pro プランは 50 req/day。テスト時は `settings.yaml` の `max_papers` を `3〜5` に下げてください。

---

## 6. キーワード管理（よく使う操作）

### 新しいキーワードの追加

`config/keywords.yaml` の `include` リストに追記するだけ。コードの変更は不要。

```yaml
include:
  - audio foundation model   # 既存
  - audio codec              # ← 追加するだけ
```

### UI カテゴリへの反映

既存カテゴリに割り当てる場合は `ui_categories` の該当カテゴリの `keywords` にも追記。

### 新カテゴリの追加

`ui_categories` に新エントリを追加。フロントエンドの `CategoryFilter` は categories データを動的に生成するため、コード変更は不要。

```yaml
ui_categories:
  - id: new_category
    label: 新カテゴリ名
    color: "#e879f9"
    keywords:
      - new keyword 1
      - new keyword 2
```

---

## 7. 参考資料

| 資料 | URL / 場所 |
|---|---|
| 要件定義書・設計書 | `system_design.md`（同梱） |
| arXiv API ドキュメント | https://arxiv.org/help/api/user-manual |
| GitHub Models ドキュメント | https://docs.github.com/en/github-models |
| GitHub Pages ドキュメント | https://docs.github.com/en/pages |
| Vite ドキュメント | https://vitejs.dev/ |
| openai Python SDK | https://github.com/openai/openai-python |

---

## 8. 引き継ぎチェックリスト

GitHub Copilot での作業開始前に以下を順番に確認してください。

| # | 確認項目 | 担当 | 状態 |
|---|---|---|---|
| 1 | リポジトリへのコミットが完了していること | 完了済み | ✅ |
| 2 | GitHub Pages が有効化されていること | Copilot | ☐ |
| 3 | Actions の `Read and write permissions` が設定されていること | Copilot | ☐ |
| 4 | DevContainer が起動できること | Copilot | ☐ |
| 5 | `test_connection.py` が成功すること | Copilot | ☐ |
| 6 | 手動 `workflow_dispatch` が全ジョブ green になること | Copilot | ☐ |
| 7 | GitHub Pages URL でフロントが表示されること | Copilot | ☐ |
| 8 | 初回の週次 JSON が `data/weekly/` に生成されること | Copilot | ☐ |
| 9 | フロントで論文データが正しく表示されること | Copilot | ☐ |
| 10 | 翌週金曜日の自動実行が成功すること | Copilot | ☐ |

---

*引き継ぎ書 以上*
