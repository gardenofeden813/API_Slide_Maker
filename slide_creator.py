import os
import json
from jinja2 import Template
from dotenv import load_dotenv 

# --- Google GenAI SDKのインポート ---
from google import genai 
from google.genai import types

# --- 1 初期設定とプロンプトの読み込み ---
load_dotenv()
# Google AI Studioで取得したキーは "GEMINI_API_KEY" で設定することを推奨
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY")) 
MODEL_NAME = "gemini-2.5-flash"

try:
    with open("prompt.txt", "r", encoding="utf-8") as f:
        prompt = f.read()
except FileNotFoundError:
    print("⚠️ 'prompt.txt' が見つかりません。ファイルを作成し、指示内容を記述してください。")
    exit()

if not prompt.strip():
    print("⚠️ 'prompt.txt' に内容が記述されていません。")
    exit()

print("--- Gemini API でスライドコンテンツ生成を開始します ---")
print(f"モデル: {MODEL_NAME}, プロンプトのテーマ: {prompt.splitlines()[0]}")

# --- 2 Geminiにスライド内容を生成させる ---
# プロンプト全体を単一の変数として作成
full_prompt = f"""
[System Prompt]: あなたは聴衆の理解を最大限に助ける、プロフェッショナルなスライド構成の専門家です。冗長な情報を避け、**図解の必要性を提案し、専門用語を強調**することで、情報の密度の高さを保ちつつ理解しやすいコンテンツを作成します。
[Task]: 次の指示に基づき、情報を集約し、HTMLスライド用のコンテンツを生成してください。
[Output Format]:
- 出力は必ずJSON形式で、全体を単一のJSON配列で構成してください。
- 各スライドは必ず以下の構造を持つオブジェクトとしてください: {{"title": "スライドタイトル", "body": "箇条書きや段落で構成された詳細な説明"}}
- bodyの内容は、以下のルールに従い、**プレゼンテーション資料として最適化**してください。
    1.  **太字強調**: 重要なキーワードや専門用語は、必ずアスタリスク2つで囲んで**太字**にしてください (例: `**エネルギー効率**`)。
    2.  **箇条書きの活用**: 3つ以上の並列情報やリストは必ず箇条書き（`-` または `*`）を使用し、分類された構造（見出し＋箇条書き）を意識してください。
    3.  **図解の提案**: 複雑な概念（例：システム構造、比較表、フロー、重要用語）を説明するスライドでは、聴衆の理解を深めるため、bodyの**冒頭に**関連する図解を提案するタグを挿入してください。タグの形式は `` とし、日本語で具体的な内容を指定してください（例: ``）。
- スライドの総数は40枚以内としてください。

[Instructions]: {prompt}
"""

try:
    # --- 💡 修正箇所: Part.from_text() の代わりに types.Part(text=...) を使用 ---
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[
            types.Content(role="user", parts=[types.Part(text=full_prompt)]),
        ],
        config=types.GenerateContentConfig(
            # JSON形式での出力を要求
            response_mime_type="application/json",
            # JSONの構造を定義
            response_schema=types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "title": types.Schema(type=types.Type.STRING, description="スライドのメインタイトル"),
                        "body": types.Schema(type=types.Type.STRING, description="スライドの具体的な内容（箇条書きを含む）")
                    },
                    required=["title", "body"]
                )
            ),
            # Web検索は無効化 (参照テキストを直接渡すため)
            # tools=[{"google_search": {}}] 
        )
    )
except Exception as e:
    # APIキーが間違っている、または環境設定に問題がある場合にここに到達します
    print(f"\n致命的なエラーが発生しました。Gemini APIの呼び出しに失敗: {e}")
    print("--- トラブルシューティングのヒント ---")
    print("1. .envファイルに 'GEMINI_API_KEY=' の形式で正しいキーが設定されているか確認してください。")
    print("2. ネットワーク接続を確認してください。")
    print("-----------------------------------")
    exit()


# --- 3 Geminiの出力を解析 ---
raw_output = response.text.strip()
slides = []

try:
    # JSONモードを使用しているため、通常はクリーンなJSONが返される
    slides = json.loads(raw_output)

    if not isinstance(slides, list):
        print("⚠️ JSONのルート要素が配列ではありませんでした。解析を中断します。")
        exit()

except json.JSONDecodeError as e:
    print(f"\n⚠️ JSONの読み取りに失敗しました。エラー: {e}")
    print("\n--- Geminiからの生出力（デバッグ用）---")
    print(raw_output)
    print("-----------------------------------")
    exit()

if not slides:
    print("⚠️ スライドコンテンツが生成されませんでした。プロンプトを見直してください。")
    exit()
    
# --- 4 & 5 HTMLテンプレートを読み込み、スライドを挿入 ---
try:
    # slide_template.html の読み込み
    with open("slide_template.html", "r", encoding="utf-8") as f:
        html_template = f.read()
    
    template = Template(html_template)
    rendered_html = template.render(slides=slides)
except FileNotFoundError:
    print("⚠️ 'slide_template.html' が見つかりません。ファイルが存在するか確認してください。")
    exit()
except Exception as e:
    print(f"⚠️ テンプレート処理中にエラーが発生しました: {e}")
    exit()


# --- 6 出力ファイル保存 ---
with open("output.html", "w", encoding="utf-8") as f:
    f.write(rendered_html)

print("\n----------------------------------------------------")
print(f"✅ output.html を生成しました！スライド数: {len(slides)}")
print("----------------------------------------------------")