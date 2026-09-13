# meeting-digest — Omi の会話を社内ツールへ取り込む

Omi Developer API から会話を**読み取り専用**で取得し、正規化して、設定した配信先
（sink）に届けます。商談・会議の記録を社内に流す用途を想定しています。

## なぜ pull 型なのか

Omi には webhook（push 型）の連携方式もありますが、このパイプラインは**あえて pull 型**です。

送信側の実装（`backend/utils/webhooks.py` の `_post_dev_webhook`）が付けるヘッダは
`Idempotency-Key` と `Content-Type` のみで、**HMAC 署名や共有シークレットがありません**。
受け取る側は、そのリクエストが本当に Omi から来たものかを暗号学的に検証できません。
商談の文字起こしを受けるエンドポイントをインターネットに公開し、URL の秘匿だけが
防御という構成になります。

pull 型は自社側に受信口を作りません。通信は常に自社サーバーから `api.omi.me` への
outbound のみで、事故時はキーを失効させれば遮断が完了します。

リアルタイム性が業務要件になった段階で webhook を足す、という順序を推奨します。
pull → push は足せますが、逆は戻しにくい設計です。

## 必要なもの

- Python 3.9 以上
- `conversations:read` **のみ**を持つ Developer API キー（app.omi.me → Developer → API Keys）

書き込みスコープは不要です。付けないでください。

## セットアップ

```bash
cd integrations/meeting-digest
python3 -m pip install -r requirements.txt
export OMI_API_KEY="omi_dev_..."
python3 -m meeting_digest --show-config   # キーは伏せて表示されます
python3 -m meeting_digest
```

既定では `out/` に会話ごとの Markdown ノートが書き出されます。

## 環境変数

| 変数 | 既定値 | 内容 |
|---|---|---|
| `OMI_API_KEY` | （必須） | Developer API キー。`omi_dev_` 以外は起動時に弾きます |
| `OMI_API_BASE` | `https://api.omi.me` | 自前バックエンドを使う場合のみ変更 |
| `MD_SINKS` | `markdown` | カンマ区切り。`markdown`, `slack` |
| `MD_OUTPUT_DIR` | `out` | Markdown の出力先 |
| `MD_STATE_PATH` | `state/processed.json` | 配信済み管理ファイル |
| `MD_LOOKBACK_HOURS` | `24` | 何時間前までを対象にするか |
| `MD_MAX_TRANSCRIPT_FETCHES` | `20` | 1回の実行で文字起こしを取得する上限 |
| `MD_LIST_PAGE_SIZE` | `50` | 一覧取得のページサイズ（1〜100） |
| `MD_CATEGORIES` | なし | カテゴリ絞り込み（カンマ区切り） |
| `MD_MARKDOWN_INCLUDE_TRANSCRIPT` | `true` | Markdown に文字起こし全文を含めるか |
| `MD_SLACK_WEBHOOK_URL` | なし | `slack` sink を使う場合に必須 |
| `MD_REQUEST_TIMEOUT_SECONDS` | `30` | HTTP タイムアウト |

## レート制限との付き合い方

Developer API はキー単位・1時間あたりで制限されます
（`backend/utils/rate_limit_config.py`）。

| 制限 | 上限/時 |
|---|---|
| `dev:conversation_reads_total`（会話読み取り全体） | 60 |
| `dev:conversations_read`（一覧） | 60 |
| `dev:conversation_detail_read`（個別取得） | 60 |
| `dev:conversation_transcript_read`（**文字起こしを含む読み取り**） | 25 |

文字起こし付きの読み取りが最も厳しい枠です。そのためこのパイプラインは

1. まず**文字起こしなし**で一覧を取得（安い枠）
2. 未配信のものだけを個別に**文字起こし付き**で取得（1回の実行で既定20件まで）

という2段階にしています。上限を超えた分は次回の実行に回されます（`deferred`）。
配信済みの会話は文字起こし枠を一切消費しません。

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

cron の例（15分ごと）:

```cron
*/15 * * * * cd /opt/meeting-digest && OMI_API_KEY=omi_dev_... /usr/bin/python3 -m meeting_digest --json >> log/run.log 2>&1
```

配信済み管理ファイル（`MD_STATE_PATH`）があるため、同じ会話が二重に配信されることはありません。

## 配信先を追加する

`meeting_digest/sinks/` にモジュールを1つ追加し、`Sink` を実装して
`sinks/__init__.py` の `build_sinks` に1行足すだけです。他のコードは変更しません。

```python
class NotionSink(Sink):
    name = "notion"

    def deliver(self, record: MeetingRecord) -> None:
        ...
```

`deliver` は**2回呼ばれても壊れない**ように実装してください。配信後・状態保存前に
実行が落ちた場合、次回に再送されます。

## 取り扱い上の注意

- `out/` と `state/` は会話の実データを含みます。`.gitignore` 済みですが、
  バックアップや共有の際は取り扱いに注意してください。
- Slack sink は**文字起こしを送りません**。要約と未完了アクションのみです。
  チャンネルは想定より広い読者と長い保持期間を持つためです。全文が必要な場合は
  Markdown ノートを参照してください。
- API キーはログにも配信内容にも出力されません（`Config.redacted()`）。
- 商談を録音する場合、相手方への告知と同意の取得は運用として必須です。

## テスト

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest tests -q
```

## 将来的な配置について

このディレクトリは omi リポジトリのファイルを一切変更しないため、本家への追従で
衝突しません。ただし自社の運用コードとして育てる場合は、**独立したリポジトリに
切り出す**ことを推奨します。omi 本体の更新頻度が高く、フォークに自社ロジックを
混ぜると追従コストが上がるためです。
