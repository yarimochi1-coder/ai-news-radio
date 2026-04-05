"""
AIニュースラジオ 自動生成スクリプト

使い方:
  python generate_radio.py

必要な環境変数:
  ANTHROPIC_API_KEY       — Claude APIキー
  LINE_CHANNEL_TOKEN      — LINE Messaging APIアクセストークン
  LINE_USER_ID            — 送信先のLINE User ID

必要なパッケージ:
  pip install anthropic edge-tts duckduckgo-search requests
"""

import os
import sys
import json
import asyncio
import shutil
import requests
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
RULES_PATH = SCRIPT_DIR / "rules" / "台本生成ルール.md"
RESEARCH_DIR = SCRIPT_DIR / "リサーチログ"
SCRIPT_OUTPUT_DIR = SCRIPT_DIR / "台本"
AUDIO_DIR = SCRIPT_DIR / "音声"

TODAY = datetime.now().strftime("%Y-%m-%d")
TODAY_JP = datetime.now().strftime("%Y年%m月%d日")

# edge-tts の日本語音声（女性: Nanami, 男性: Keita）
TTS_VOICE = "ja-JP-NanamiNeural"

# Claude モデル
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# iCloud Drive（ローカル実行時のみ使用）
ICLOUD_DIR = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "AIニュースラジオ"

# GitHub Actions上かどうか
IS_CI = os.environ.get("CI") == "true"


# ---------------------------------------------------------------------------
# 1. ニュースリサーチ（DuckDuckGo検索）
# ---------------------------------------------------------------------------
def search_news() -> list[dict]:
    """DuckDuckGoでAI関連ニュースを検索して結果を返す"""
    from duckduckgo_search import DDGS

    queries = [
        "AI news today 2026",
        "LLM new release announcement",
        "Claude Anthropic update",
        "OpenAI GPT news",
        "Google Gemini update",
        "AI business automation news",
        "生成AI ニュース 最新",
        "AI 業務活用 最新事例",
    ]

    all_results = []
    seen_urls = set()

    with DDGS() as ddgs:
        for query in queries:
            try:
                results = list(ddgs.news(query, max_results=5, timelimit="d"))
                for r in results:
                    if r.get("url") not in seen_urls:
                        seen_urls.add(r["url"])
                        all_results.append({
                            "title": r.get("title", ""),
                            "body": r.get("body", ""),
                            "url": r.get("url", ""),
                            "source": r.get("source", ""),
                            "date": r.get("date", ""),
                        })
            except Exception as e:
                print(f"  検索エラー ({query}): {e}")

    print(f"  合計 {len(all_results)} 件のニュースを収集しました")

    # 前日までのリサーチログと重複排除
    past_titles = set()
    past_urls = set()
    for log_file in RESEARCH_DIR.glob("*_リサーチ.json"):
        if log_file.name.startswith(TODAY):
            continue  # 今日のログはスキップ
        try:
            past_data = json.loads(log_file.read_text(encoding="utf-8"))
            for item in past_data:
                past_titles.add(item.get("title", "").strip().lower())
                past_urls.add(item.get("url", ""))
        except Exception:
            pass

    filtered = []
    for r in all_results:
        title_lower = r["title"].strip().lower()
        if r["url"] in past_urls or title_lower in past_titles:
            continue
        filtered.append(r)

    removed = len(all_results) - len(filtered)
    if removed > 0:
        print(f"  前日以前と重複する {removed} 件を除外しました")
    print(f"  最終: {len(filtered)} 件のニュース")
    return filtered


# ---------------------------------------------------------------------------
# 2. 台本生成（Claude API）
# ---------------------------------------------------------------------------
def generate_script(news_data: list[dict]) -> tuple[str, str]:
    """ニュースデータから台本を生成する。(台本マークダウン, 読み上げテキスト)を返す"""
    import anthropic

    client = anthropic.Anthropic()

    rules = RULES_PATH.read_text(encoding="utf-8")

    news_text = ""
    for i, n in enumerate(news_data, 1):
        news_text += f"\n### ニュース{i}\n"
        news_text += f"- タイトル: {n['title']}\n"
        news_text += f"- 概要: {n['body']}\n"
        news_text += f"- ソース: {n['source']}\n"
        news_text += f"- URL: {n['url']}\n"
        news_text += f"- 日付: {n['date']}\n"

    prompt = f"""あなたはAIニュースラジオのパーソナリティです。
以下のルールと収集したニュースデータをもとに、今日（{TODAY_JP}）の台本を作成してください。

## 台本生成ルール
{rules}

## 収集したニュースデータ
{news_text}

## 出力指示
以下の2つを出力してください:

### PART1: 台本（マークダウン形式）
番組の台本をマークダウン形式で書いてください。

### PART2: 読み上げテキスト
TTS（テキスト読み上げ）用のプレーンテキストを書いてください。
- マークダウン記法は除去
- 英語の固有名詞にはカタカナ読みを括弧で付与しないでください。カタカナのみにしてください。
  例: Claude → クロード、OpenAI → オープンエーアイ、Google → グーグル、Anthropic → アンソロピック
  例: GPT → ジーピーティー、API → エーピーアイ、LLM → エルエルエム
- 数字は漢数字ではなくそのまま使ってOK
- 自然な話し言葉で、間（ま）を意識して句読点を入れる
- 「、」で短い間、「。」で長い間を作る

必ず「===PART1===」と「===PART2===」で区切って出力してください。
"""

    print("  Claude APIで台本を生成中...")
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text

    # PART1とPART2を分離
    if "===PART1===" in text and "===PART2===" in text:
        parts = text.split("===PART2===")
        script_md = parts[0].replace("===PART1===", "").strip()
        reading_text = parts[1].strip()
    else:
        script_md = text
        reading_text = text

    return script_md, reading_text


# ---------------------------------------------------------------------------
# 3. 音声化（edge-tts）
# ---------------------------------------------------------------------------
async def generate_audio(text: str, output_path: Path):
    """edge-ttsでテキストを音声ファイル（MP3）に変換"""
    import edge_tts

    communicate = edge_tts.Communicate(text, TTS_VOICE, rate="+10%")
    await communicate.save(str(output_path))


# ---------------------------------------------------------------------------
# 4. LINE送信
# ---------------------------------------------------------------------------
def send_line_audio(audio_url: str, duration_ms: int, script_summary: str):
    """LINE Messaging APIで音声メッセージ+テキストを送信"""
    token = os.environ.get("LINE_CHANNEL_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")

    if not token or not user_id:
        print("  LINE設定なし — スキップ")
        return

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    # テキスト + 音声の2メッセージを送信
    body = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": f"🎙 AIニュースラジオ {TODAY_JP}\n\n{script_summary}\n\n▶ 下の音声を再生してください",
            },
            {
                "type": "audio",
                "originalContentUrl": audio_url,
                "duration": duration_ms,
            },
        ],
    }

    resp = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers=headers,
        json=body,
    )

    if resp.status_code == 200:
        print("  LINE送信完了!")
    else:
        print(f"  LINE送信エラー: {resp.status_code} {resp.text}")


def send_line_text(message: str):
    """LINEにテキストメッセージのみ送信（音声URLが使えない場合のフォールバック）"""
    token = os.environ.get("LINE_CHANNEL_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")

    if not token or not user_id:
        return

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    body = {
        "to": user_id,
        "messages": [{"type": "text", "text": message}],
    }

    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers=headers,
        json=body,
    )


def get_audio_duration_ms(file_path: Path) -> int:
    """MP3ファイルの長さをミリ秒で概算する"""
    file_size = file_path.stat().st_size
    # edge-tts の MP3 は約 32kbps
    bitrate = 32000
    duration_sec = (file_size * 8) / bitrate
    return int(duration_sec * 1000)


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------
def main():
    print(f"=== AIニュースラジオ生成 ({TODAY_JP}) ===\n")

    # ディレクトリ作成
    RESEARCH_DIR.mkdir(exist_ok=True)
    SCRIPT_OUTPUT_DIR.mkdir(exist_ok=True)
    AUDIO_DIR.mkdir(exist_ok=True)

    # APIキー確認
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("エラー: ANTHROPIC_API_KEY が設定されていません")
        sys.exit(1)

    # --- Step 1: ニュースリサーチ ---
    print("[1/4] ニュースを検索中...")
    news_data = search_news()

    if not news_data:
        print("ニュースが見つかりませんでした。終了します。")
        send_line_text(f"⚠ AIニュースラジオ ({TODAY_JP})\nニュースが見つかりませんでした。")
        sys.exit(1)

    # リサーチログ保存
    research_path = RESEARCH_DIR / f"{TODAY}_リサーチ.json"
    research_path.write_text(
        json.dumps(news_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  リサーチログ保存: {research_path.name}")

    # --- Step 2: 台本生成 ---
    print("\n[2/4] 台本を生成中...")
    script_md, reading_text = generate_script(news_data)

    # 台本保存
    script_path = SCRIPT_OUTPUT_DIR / f"{TODAY}_台本.md"
    script_path.write_text(script_md, encoding="utf-8")
    print(f"  台本保存: {script_path.name}")

    reading_path = SCRIPT_OUTPUT_DIR / f"{TODAY}_読み上げ.txt"
    reading_path.write_text(reading_text, encoding="utf-8")
    print(f"  読み上げテキスト保存: {reading_path.name}")

    # 台本サマリー（LINE通知用に最初の数行を抜粋）
    summary_lines = script_md.split("\n")
    summary = ""
    for line in summary_lines:
        if line.startswith("## メインニュース") or line.startswith("### 見出し"):
            continue
        if "見出し" in line or line.startswith("##"):
            continue
        if line.strip() and not line.startswith("#"):
            summary += line.strip() + "\n"
            if len(summary) > 200:
                break
    # シンプルにトピック名を抽出
    topics = [l.replace("## ", "").strip() for l in summary_lines if l.startswith("## メインニュース")]
    summary = "\n".join(f"• {t}" for t in topics) if topics else summary[:200]

    # --- Step 3: 音声生成 ---
    print("\n[3/4] 音声を生成中...")
    audio_path = AUDIO_DIR / f"{TODAY}_AIニュースラジオ.mp3"
    asyncio.run(generate_audio(reading_text, audio_path))
    print(f"  音声ファイル保存: {audio_path.name}")

    # --- Step 4: 配信 ---
    print("\n[4/4] 配信中...")
    duration_ms = get_audio_duration_ms(audio_path)

    if IS_CI:
        # GitHub Actions: GitHub Pages経由でLINE送信
        # audio_urlは GitHub Actions workflow で設定される環境変数から取得
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        audio_url = f"https://{repo.split('/')[0]}.github.io/{repo.split('/')[1]}/audio/{TODAY}_AIニュースラジオ.mp3" if repo else ""

        if audio_url:
            send_line_audio(audio_url, duration_ms, summary)
        else:
            send_line_text(f"🎙 AIニュースラジオ {TODAY_JP}\n\n{summary}\n\n※ 音声はGitHubで確認してください")
    else:
        # ローカル: iCloud Driveにコピー
        try:
            ICLOUD_DIR.mkdir(exist_ok=True)
            icloud_path = ICLOUD_DIR / f"{TODAY}_AIニュースラジオ.mp3"
            shutil.copy2(audio_path, icloud_path)
            print(f"  iCloud Driveにコピー完了: {icloud_path.name}")
        except Exception as e:
            print(f"  iCloudコピースキップ: {e}")

        # ローカルでもLINE送信可能（トークンがあれば）
        if os.environ.get("LINE_CHANNEL_TOKEN"):
            send_line_text(f"🎙 AIニュースラジオ {TODAY_JP}\n\n{summary}\n\n🎧 iCloud Drive → AIニュースラジオ で聴けます")

    # 完了
    file_size_mb = audio_path.stat().st_size / (1024 * 1024)
    print(f"\n=== 完了 ===")
    print(f"  台本: {script_path}")
    print(f"  音声: {audio_path} ({file_size_mb:.1f} MB)")
    print(f"  尺: 約{duration_ms // 60000}分{(duration_ms % 60000) // 1000}秒")


if __name__ == "__main__":
    main()
