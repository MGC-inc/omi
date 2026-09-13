# meeting-digest — Omi の会話を社内で使えるようにする

Omi Developer API から会話を**読み取り専用**で取得し、正規化して、社内の配信先に届けます。
さらに取り込んだデータをローカルに蓄積し、他ツールから叩ける読み取り専用 API として公開できます。

```
                    ┌─ Markdown ノート（ローカル）
                    ├─ Notion（1会話 = 1ページ）
Omi ──pull──> 取り込み ─┼─ Slack（要約のみ）
                    └─ SQLite ──> 読み取り専用 HTTP API ──> n8n / 社内システム / 他AI
```

## 3つのコマンド

| コマンド | 役割 |
|---|---|
| `python -m meeting_digest ingest` | 新しい会話を取得して各配信先に届ける（既定） |
| `python -m meeting_digest daily` | 1日分の振り返りを作って届ける |
| `python -m meeting_digest serve` | 蓄積したデータを読み取り専用 API として公開する |

## なぜ pull 型なのか

Omi には webhook（push 型）の連携方式もありますが、このパイプラインは**あえて pull 型**です。

送信側の実装（`backend/utils/webhooks.py` の `_post_dev_webhook`）が付けるヘッダは
`Idempotency-Key` と `Content-Type` のみで、**HMAC 署名や共有シークレットがありません**。
受け取る側は、そのリクエストが本当に Omi から来たものかを暗号学的に検証できません。
商談の文字起こしを受けるエンドポイントをインターネットに公開し、URL の秘匿だけが
防御という構成になります。

pull 型は自社側に受信口を作りません。通信は常に自社サーバーから `api.omi.me` への
outbound のみで、事故時はキーを失効させれば遮断が完了します。

## 必要なもの

- Python 3.9 以上
- `conversations:read` **のみ**を持つ Developer API キー（app.omi.me → Developer → API Keys）

書き込みスコープは不要です。付けないでください。

## セットアップ

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt          # API を使う場合は requirements-server.txt
export OMI_API_KEY="omi_dev_..."
python -m meeting_digest --show-config   # キーは伏せて表示されます
python -m meeting_digest
```

既定では `out/` に会話ごとの Markdown ノートが書き出されます。

## 環境変数

### 基本

| 変数 | 既定値 | 内容 |
|---|---|---|
| `OMI_API_KEY` | （必須） | Developer API キー。`omi_dev_` 以外は起動時に弾きます |
| `OMI_API_BASE` | `https://api.omi.me` | 自前バックエンドを使う場合のみ変更 |
| `MD_SINKS` | `markdown` | カンマ区切り。`markdown`, `notion`, `slack`, `store` |
| `MD_LOOKBACK_HOURS` | `24` | 何時間前までを対象にするか |
| `MD_MAX_TRANSCRIPT_FETCHES` | `20` | 1回の実行で文字起こしを取得する上限 |
| `MD_LIST_PAGE_SIZE` | `50` | 一覧取得のページサイズ（1〜100） |
| `MD_CATEGORIES` | なし | カテゴリ絞り込み（カンマ区切り） |
| `MD_UTC_OFFSET_HOURS` | `9` | 「その日」の区切りと表示時刻の基準。日本なら 9 |
| `MD_STATE_PATH` | `state/processed.json` | 配信済み管理ファイル |
| `MD_REQUEST_TIMEOUT_SECONDS` | `30` | HTTP タイムアウト |

### 配信先ごと

| 変数 | 内容 |
|---|---|
| `MD_OUTPUT_DIR` | Markdown の出力先（既定 `out`） |
| `MD_MARKDOWN_INCLUDE_TRANSCRIPT` | Markdown に文字起こし全文を含めるか（既定 `true`） |
| `MD_NOTION_TOKEN` | Notion インテグレーショントークン |
| `MD_NOTION_DATABASE_ID` | 保存先データベース ID。Notion の URL をそのまま貼っても解釈します |
| `MD_NOTION_INCLUDE_TRANSCRIPT` | Notion ページに文字起こしを含めるか（既定 `true`） |
| `MD_SLACK_WEBHOOK_URL` | Slack Incoming Webhook の URL |
| `MD_STORE_PATH` | SQLite の保存先（既定 `state/conversations.db`） |

### API サーバー

| 変数 | 既定値 | 内容 |
|---|---|---|
| `MD_API_TOKEN` | （`serve` に必須） | Bearer トークン。24文字以上。未設定なら起動を拒否します |
| `MD_API_HOST` | `127.0.0.1` | 待ち受けアドレス。外部公開は明示的な変更が必要 |
| `MD_API_PORT` | `8787` | 待ち受けポート |

## レート制限との付き合い方

Developer API はキー単位・1時間あたりで制限されます（`backend/utils/rate_limit_config.py`）。

| 制限 | 上限/時 |
|---|---|
| `dev:conversation_reads_total`（会話読み取り全体） | 60 |
| `dev:conversations_read`（一覧） | 60 |
| `dev:conversation_detail_read`（個別取得） | 60 |
| `dev:conversation_transcript_read`（**文字起こしを含む読み取り**） | 25 |

文字起こし付きの読み取りが最も厳しい枠です。そのため `ingest` は

1. まず**文字起こしなし**で一覧を取得（安い枠）
2. 未配信のものだけを個別に**文字起こし付き**で取得（1回の実行で既定20件まで）

という2段階にしています。上限を超えた分は次回の実行に回されます（`deferred`）。
配信済みの会話は文字起こし枠を一切消費しません。

`daily` は一覧取得だけで完結するため、**文字起こし枠を消費しません**。何度でも再実行できます。

## Notion 連携

### データベースの用意

Notion で新しいデータベースを作り、インテグレーションに接続を許可してください
（データベース右上の「…」→「接続」→ 作成したインテグレーションを追加）。

プロパティは**タイトル列だけでも動きます**。以下を足すと対応する値が埋まります
（存在しない列は自動的に飛ばされるので、必要なものだけ追加してください）。

| プロパティ名 | 型 | 入る値 |
|---|---|---|
| （タイトル列・名前は任意） | Title | 会話のタイトル |
| `Date` | Date | 開始〜終了 |
| `Category` | Select | Omi のカテゴリ |
| `Duration (min)` | Number | 長さ（分） |
| `Open Actions` | Number | 未完了アクション数 |
| `Language` | Select | 言語 |
| `Omi ID` | Text | Omi の会話 ID |

`Omi ID` を用意すると、**同じ会話のページが二重に作られなくなります**。日次の振り返りも
同じ列を使って前回分を差し替えるため、追加を推奨します。

### 実行

```bash
export MD_SINKS="markdown,notion"
export MD_NOTION_TOKEN="ntn_..."
export MD_NOTION_DATABASE_ID="https://www.notion.so/xxxx/1234...?v=..."   # URL のままで可
python -m meeting_digest
```

## Slack 連携

Slack App の Incoming Webhook を作成し、URL を設定します。

```bash
export MD_SINKS="markdown,slack"
export MD_SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
python -m meeting_digest
```

Slack には**文字起こしを送りません**。要約と未完了アクションのみです。チャンネルは
想定より読者が広く保持期間も長いためです。全文が必要な場合は Markdown ノートか API を
参照してください。

## 1日の振り返り

```bash
python -m meeting_digest daily                  # 前日（既定）
python -m meeting_digest daily --day today      # 当日
python -m meeting_digest daily --day 2026-09-12 # 指定日
```

`MD_UTC_OFFSET_HOURS`（既定 9）を基準にした**その日のローカル暦日**で集計します。
朝8時の会話は前日ではなくその日の朝として扱われます。

内容は会話数・合計時間・カテゴリ内訳・未完了アクション一覧（期限順）・予定・各会話の概要です。
Omi がすでに生成した要約だけを使うため、追加の LLM 呼び出しも費用も発生しません。

Markdown は `out/daily/YYYY-MM-DD.md`、Slack と Notion にも配信されます
（`store` は日次に対応しません。API から都度取得できるためです）。

## 他ツールから叩く API

蓄積したデータを読み取り専用の HTTP API として公開します。**Omi のキーを配らずに**
他のツールへデータを渡せます。

```bash
export MD_SINKS="markdown,store"          # store を有効にしてデータを蓄積
python -m meeting_digest                  # 取り込み

export MD_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
pip install -r requirements-server.txt
python -m meeting_digest serve            # http://127.0.0.1:8787
```

| エンドポイント | 内容 |
|---|---|
| `GET /health` | 死活監視。**認証不要**、会話データは一切含みません |
| `GET /v1/conversations` | 一覧。`limit` `offset` `since` `until` `category` `q` `include_transcript` |
| `GET /v1/conversations/{id}` | 1件（文字起こし込み） |
| `GET /v1/action-items` | アクション一覧。`open_only` `due_before` |
| `GET /v1/daily/{YYYY-MM-DD}` | その日の振り返り（JSON） |
| `GET /v1/stats` | 蓄積件数 |

`/health` 以外は `Authorization: Bearer <MD_API_TOKEN>` が必須です。

```bash
curl -H "Authorization: Bearer $MD_API_TOKEN" "http://127.0.0.1:8787/v1/conversations?limit=5"

# 日本語で検索する場合は URL エンコードが必要です
curl -G -H "Authorization: Bearer $MD_API_TOKEN" \
  --data-urlencode "q=商談" "http://127.0.0.1:8787/v1/conversations"
```

OpenAPI ドキュメントは `http://127.0.0.1:8787/docs` で見られます。

### セキュリティ設計

このプロジェクトで唯一の**受信口**なので、閉じる方向に倒してあります。

- **トークン必須** — 未設定なら起動しません。認証なしで公開される瞬間が存在しません
- トークン比較は `hmac.compare_digest`（タイミング攻撃対策）
- 既定の待ち受けは `127.0.0.1`。ネットワークに晒すのは明示的な操作が必要です
- 全エンドポイントが読み取り専用。Omi のデータもローカルの蓄積も変更できません
- 外部公開する場合は、リバースプロキシでの TLS 終端と IP 制限を併用してください

## 実行状況の読み方

```json
{ "listed": 12, "already_delivered": 9, "fetched": 3, "delivered": 3, "deferred": 0,
  "failures": [], "rate_limited": false }
```

- `already_delivered` — 前回までに配信済みでスキップした件数
- `deferred` — 今回の上限またはレート制限で次回に回した件数
- `rate_limited` — 途中で枠を使い切った。異常ではなく、次回の実行で続きから処理されます

終了コードは 0（正常）、1（取得または配信に失敗あり）、2（設定不備）です。

## 定期実行

```cron
# 15分ごとに取り込み
*/15 * * * * cd /opt/meeting-digest && . .venv/bin/activate && python -m meeting_digest --json >> log/ingest.log 2>&1

# 毎朝7時に前日の振り返り
0 7 * * * cd /opt/meeting-digest && . .venv/bin/activate && python -m meeting_digest daily --json >> log/daily.log 2>&1
```

環境変数は cron から見えないため、`.env` を読み込むか cron 行に直接書いてください。

配信済み管理ファイル（`MD_STATE_PATH`）があるため、同じ会話が二重に配信されることはありません。

## 配信先を追加する

`meeting_digest/sinks/` にモジュールを1つ追加し、`Sink` を実装して
`sinks/__init__.py` の `build_sinks` に1行足すだけです。他のコードは変更しません。

```python
class MyToolSink(Sink):
    name = "mytool"
    supports_daily = False   # 日次にも対応するなら True にして deliver_daily を実装

    def deliver(self, record: MeetingRecord) -> None:
        ...
```

`deliver` は**2回呼ばれても壊れない**ように実装してください。配信後・状態保存前に
実行が落ちた場合、次回に再送されます。

## 取り扱い上の注意

- `out/`、`state/`、`*.db` は会話の実データを含みます。`.gitignore` 済みですが、
  バックアップや共有の際は取り扱いに注意してください
- API キーとトークンはログにも配信内容にも出力されません（`Config.redacted()`）
- 商談を録音する場合、相手方への告知と同意の取得は運用として必須です

## テスト

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

## 将来的な配置について

このディレクトリは omi リポジトリのファイルを一切変更しないため、本家への追従で
衝突しません。ただし自社の運用コードとして育てる場合は、**独立したリポジトリに
切り出す**ことを推奨します。omi 本体の更新頻度が高く、フォークに自社ロジックを
混ぜると追従コストが上がるためです。
