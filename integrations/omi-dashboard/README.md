# omi-dashboard

Omi の会話・タスク・アイデアを見るための社内ダッシュボード（Next.js）。

```
Omi Developer API ──┐
                     ├──> このアプリ（サーバー側のみ） ──> 1人だけが見るダッシュボード
Notion「Omi 会話ログ」┘
```

## データ源について

- **Omi Developer API が主な取得先です。** 一覧取得エンドポイントは、文字起こしを含めなくても
  各会話の `structured`（タイトル・概要・カテゴリ・セクション・アクションアイテム・予定）を
  返すため、集計・会話一覧・タスク・アイデアのビューはすべてここから作られます
  （`src/lib/omi.ts`）。文字起こしだけは別で、会話詳細ページで明示的に「文字起こしを表示」を
  押したときにだけ取得します（25/時間の制限がある枠を無駄に消費しないため）
- **Notion「Omi 会話ログ」DB は補助的な役割です。** どの会話が「残す価値がある」と判断されて
  保存されたかを示す「Notion に保存済み」バッジとリンクのためだけに使います
  （`src/lib/notion.ts`）。Notion が落ちていても、未設定でも、ダッシュボード本体は壊れません
  （バッジが出ないだけです）
- 専用のタスクDB・営業ナレッジDBがある場合（`integrations/meeting-digest` の `notion_tasks` /
  `notion_sales` シンクで作られるもの）は `NOTION_TASKS_DATABASE_ID` /
  `NOTION_SALES_DATABASE_ID` を設定すると、その内容も取り込まれます。今のところ実在しないため、
  未設定でも問題なく動きます
- **英語などの表示は Gemini で日本語に翻訳されます（補助機能）。** タイトル・要約・セクション・
  タスクの説明が対象です（担当者名は翻訳しません）。`GEMINI_API_KEY` が未設定、または翻訳の
  呼び出しが失敗した場合は、元の言語のまま表示されるだけで、ダッシュボード本体は壊れません
  （`src/lib/translate.ts`）。同じ文章はサーバーがウォームな間はメモリ上にキャッシュされ、
  2回目以降は Gemini を呼び出しません（API 利用量を抑えるため）

## 画面

| パス | 内容 |
|---|---|
| `/` | 概要（今日・直近7日の件数と時間、日別/週別グラフ、カテゴリ内訳） |
| `/conversations` | 会話一覧（キーワード検索・カテゴリ絞り込み） |
| `/conversations/[id]` | 会話詳細（概要・セクション・アクションアイテム・予定・文字起こしは要求時に取得） |
| `/tasks` | 未完了タスク一覧（期限が近い順） |
| `/ideas` | アイデア・決定事項の一覧（各会話の概要・セクション。営業ナレッジDBがあればそれも表示） |

白背景・黒文字固定（OSのダークモード設定に関わらず変わりません）。アクセント色は青
`#2D6CDF` のみです。モバイル幅では下部タブバー、デスクトップ幅では上部ナビに切り替わります。

## アクセス制限

**1人だけが使う前提の、共有パスワード方式です。** ユーザー管理はありません。

- パスワード入力 → 署名付きセッションクッキー（30日間有効）を発行します
  （`src/lib/auth.ts`、`src/middleware.ts`）
- `/login` 以外のすべてのパス（API ルートも含む）が保護されます
- より強く守りたい場合は、Vercel の Deployment Protection
  （Password Protection や Vercel Authentication。Pro プラン以上）を併用してください。
  このアプリ自体のパスワード保護はプランに関わらず動きます

## セットアップ

```bash
cd integrations/omi-dashboard
npm install
cp .env.example .env.local   # 値を入れる。.env* は gitignore 済みでコミットされません
npm run dev
```

### 必要な環境変数

| 変数 | 必須 | 内容 |
|---|---|---|
| `OMI_API_KEY` | ✅ | Developer API キー（`conversations:read` のみで十分） |
| `OMI_API_BASE` | | 既定 `https://api.omi.me` |
| `NOTION_TOKEN` | ✅ | Notion の内部インテグレーショントークン（`ntn_...`） |
| `NOTION_DATABASE_ID` | ✅ | 「Omi 会話ログ」データベースのID（URLを貼ってもOK） |
| `NOTION_TASKS_DATABASE_ID` | | 専用タスクDBがあれば |
| `NOTION_SALES_DATABASE_ID` | | 専用営業ナレッジDBがあれば |
| `DASHBOARD_PASSWORD` | ✅ | ログインパスワード。長く・ランダムに |
| `SESSION_SECRET` | ✅ | セッションクッキーの署名用。長く・ランダムに |
| `DASHBOARD_UTC_OFFSET_HOURS` | | 既定 `9`（JST）。「その日」の区切りに使う |
| `DASHBOARD_LOOKBACK_DAYS` | | 既定 `60`。集計・一覧・検索の対象期間 |
| `GEMINI_API_KEY` | | Google Gemini の API キー（[Google AI Studio](https://aistudio.google.com)で発行）。タイトル・要約・タスク・アイデアなどの英語表示を日本語に翻訳するのに使う。未設定なら翻訳せず元の言語のまま表示（エラーにはならない） |
| `TRANSLATE_MODEL` | | 既定 `gemini-3.5-flash-lite` |
| `GEMINI_API_BASE` | | 既定 `https://generativelanguage.googleapis.com` |

Notion インテグレーションは、対象のデータベース側で「接続」を許可しないとアクセスできません
（データベース右上の「…」→「接続」→ 作成したインテグレーションを追加）。

`DASHBOARD_PASSWORD` / `SESSION_SECRET` の生成例:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(16))'   # パスワード用
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'   # SESSION_SECRET用
```

## Vercel へのデプロイ

このディレクトリは `omi` フォークのモノレポ内にありますが、デプロイ単位としては独立しています。
Vercel プロジェクトの **Root Directory を `integrations/omi-dashboard` に設定**してください
（以前の `meeting-digest` という名前の Vercel プロジェクトが Root Directory を
リポジトリのルートのままにしていたために失敗していたのと同じ設定項目です — そちらは
Python の CLI ツールで、そもそも Vercel にデプロイするものではありません）。

1. Vercel でこのリポジトリ（`MGC-inc/omi`）を Import
2. **Root Directory**: `integrations/omi-dashboard`
3. Framework は自動検出（Next.js）のままで問題ありません
4. 上の環境変数をすべて Project Settings → Environment Variables に設定
5. Deploy

デプロイ後、必要であれば Project Settings → Deployment Protection で
Password Protection / Vercel Authentication も有効にしてください（任意・二重の保護）。

## 動作確認について

このアプリは1人だけが使う社内ツールのため、自動テストは最小限です。代わりに、実際に
ブラウザで操作して確認しています（ローカルのモック API サーバーに対して）:

- ログイン（誤ったパスワード→エラー表示、正しいパスワード→リダイレクト）、ログアウト
- 概要ページの集計（今日・直近7日の件数と時間、カテゴリ内訳、グラフの描画）
- 会話一覧・キーワード検索・カテゴリ絞り込み
- 会話詳細（概要・セクション・アクションアイテム・完了済みの表示）、文字起こしの遅延取得
- タスク一覧（完了済みを除外、期限順）
- アイデア一覧（概要・セクションの表示）
- モバイル幅での下部タブバー / デスクトップ幅での上部ナビの切り替え
- Notion の認証情報が無効な場合でも、ダッシュボード本体（Omi 側のデータ）が壊れずに表示される
  こと（Notion は補助的な役割なので、失敗しても「保存済み」バッジが出ないだけに留まる設計）
- 英語の会話タイトル・要約・セクション・タスクが Gemini 経由で日本語に翻訳されて表示されること
  （担当者名は翻訳対象から除外）、`GEMINI_API_KEY` 未設定時は元の言語のまま表示されること
  （こちらも Notion と同じ、失敗/未設定が本体を壊さない設計）、同一リクエスト内・同一インスタンス
  内での再読み込みで Gemini 呼び出しが増えないこと（キャッシュの効果をモックサーバーのログで確認）

## 既知の制限・今後の改善候補

- `src/middleware.ts` は Next.js 16 で非推奨になった命名です（`proxy.ts` への移行が
  推奨されていますが動作に影響はありません）。手が空いたタイミングで
  `npx @next/codemod@canary middleware-to-proxy` での移行を検討してください
- アイデア・決定事項・営業メモは、専用のプロパティが Omi API・Notion のどちらにも
  まだ無いため、各会話の概要・セクションをそのまま一覧表示する形にしています。
  専用の営業ナレッジDB（`NOTION_SALES_DATABASE_ID`）を作れば、そちらも上部に表示されます
