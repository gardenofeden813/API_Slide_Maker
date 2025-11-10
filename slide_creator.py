import os
import json
import shutil
from pathlib import Path
from textwrap import shorten
from jinja2 import Template
from dotenv import load_dotenv

# --- Google GenAI SDKのインポート ---
from google import genai
from google.genai import types

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

# --- 1 初期設定とプロンプトの読み込み ---
load_dotenv()
# Google AI Studioで取得したキーは "GEMINI_API_KEY" で設定することを推奨
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MODEL_NAME = "gemini-2.5-flash"

RESOURCE_DIR = Path("resources")
PDF_PATH = RESOURCE_DIR / "water_heater_guide.pdf"
IMAGE_DIR = RESOURCE_DIR / "images"


def iter_pdf_candidates():
    """Yield plausible local PDF locations in priority order."""

    seen = set()
    queue = []

    def add(path_like):
        if not path_like:
            return
        try:
            path_obj = Path(path_like).expanduser()
        except TypeError:
            return
        resolved = path_obj.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        queue.append(path_obj)

    # 1. The cached resources file itself.
    add(PDF_PATH)

    # 2. User-specified override via environment variable.
    add(os.getenv("SOURCE_PDF_PATH"))

    # 3. Windows absolute path when the project is checked out alongside the asset.
    add(r"C:\Users\TA96939\Documents\CanadianWaterHeaterResearchProject\WaterHeaterGuide_e.pdf")

    # 4. Repository committed variants.
    add("WaterHeaterGuide_e.pdf")
    add(RESOURCE_DIR / "WaterHeaterGuide_e.pdf")

    for candidate in queue:
        yield candidate


def ensure_pdf_available() -> Path:
    """Ensure a local PDF asset exists and copy it into the cache if needed."""

    RESOURCE_DIR.mkdir(exist_ok=True)

    for candidate in iter_pdf_candidates():
        if candidate.is_file():
            if candidate.resolve() != PDF_PATH.resolve():
                shutil.copyfile(candidate, PDF_PATH)
                print(
                    f"ℹ️ 既存のPDF {candidate} を {PDF_PATH} として利用します。"
                )
            else:
                print(f"ℹ️ 既存のPDF {PDF_PATH} を使用します。")
            return PDF_PATH
        if candidate.exists():
            print(
                f"⚠️ {candidate} はファイルではないため、PDFとしては利用できません。"
            )

    raise FileNotFoundError(
        "参照用PDFが見つかりません。プロジェクトフォルダに 'WaterHeaterGuide_e.pdf' を配置するか、"
        "環境変数 SOURCE_PDF_PATH で場所を指定してください。"
    )


def extract_images_from_pdf(pdf_path: Path):
    """Extract images using PyMuPDF if available and return catalog metadata."""

    if fitz is None:
        print(
            "⚠️ PyMuPDF がインストールされていないため、PDFからの画像抽出をスキップします。\n"
            "    -> 'pip install pymupdf' を実行後に再度スクリプトを実行すると画像を利用できます。"
        )
        return {}

    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    catalog = {}

    with fitz.open(pdf_path) as doc:
        for page_index, page in enumerate(doc, start=1):
            page_text = page.get_text("text").strip()
            context_excerpt = shorten(
                " ".join(page_text.split()), width=240, placeholder="…"
            )
            images = page.get_images(full=True)
            if not images:
                continue

            for img_index, img in enumerate(images, start=1):
                xref = img[0]
                base_name = f"page-{page_index:03d}-image-{img_index:02d}"
                pix = fitz.Pixmap(doc, xref)

                if pix.n >= 5:  # CMYKなど
                    pix_converted = fitz.Pixmap(fitz.csRGB, pix)
                    pix = pix_converted
                elif pix.alpha:
                    pix_converted = fitz.Pixmap(fitz.csRGB, pix)
                    pix = pix_converted

                image_path = IMAGE_DIR / f"{base_name}.png"
                pix.save(image_path.as_posix())
                pix = None  # free resources

                catalog[base_name] = {
                    "src": image_path.as_posix(),
                    "page": page_index,
                    "width": img[2],
                    "height": img[3],
                    "context": context_excerpt,
                }

    if catalog:
        print(
            f"✅ PDFから {len(catalog)} 件の画像を抽出しました。スライドに必要な画像を image_refs で指定できます。"
        )
    else:
        print("ℹ️ PDFから抽出できる画像はありませんでした。")

    return catalog

try:
    with open("prompt.txt", "r", encoding="utf-8") as f:
        prompt = f.read()
except FileNotFoundError:
    print("⚠️ 'prompt.txt' が見つかりません。ファイルを作成し、指示内容を記述してください。")
    exit()

if not prompt.strip():
    print("⚠️ 'prompt.txt' に内容が記述されていません。")
    exit()

try:
    pdf_path = ensure_pdf_available()
except FileNotFoundError as exc:
    print(f"⚠️ {exc}")
    exit()

image_catalog = extract_images_from_pdf(pdf_path)

image_context_lines = []
for image_id, meta in sorted(image_catalog.items()):
    context = meta.get("context") or "ページ周辺のテキスト情報は取得できませんでした。"
    image_context_lines.append(
        f"- ID: {image_id} | ページ: {meta.get('page')} | 概要: {context}"
    )

image_context_section = (
    "[PDF Image Catalog]:\n" + "\n".join(image_context_lines)
    if image_context_lines
    else "[PDF Image Catalog]: 利用可能な画像は抽出されませんでした。"
)

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
        3.  **スピークラインの短文化**: 箇条書きの各行は「名詞＋キーワード」中心の**短いフレーズ**とし、1行40文字以内を目安にしてください。
        4.  **図解の提案**: 複雑な概念（例：システム構造、比較表、フロー、重要用語）を説明するスライドでは、聴衆の理解を深めるため、bodyの**冒頭に**関連する図解を提案するタグを挿入してください。タグの形式は `` とし、日本語で具体的な内容を指定してください（例: ``）。
        5.  **PDF画像の活用**: 参照PDFから抽出した図版を使う場合は `"image_refs": ["<ID>", ...]` を追加し、IDは下記カタログから選択してください。
    - スライドの総数は40枚以内としてください。

[Supporting Assets]:
{image_context_section}

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
                        "body": types.Schema(type=types.Type.STRING, description="スライドの具体的な内容（箇条書きを含む）"),
                        "image_refs": types.Schema(
                            type=types.Type.ARRAY,
                            description="抽出したPDF画像IDの配列。関連図版を利用する場合のみ指定。",
                            items=types.Schema(type=types.Type.STRING),
                        ),
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
    rendered_html = template.render(slides=slides, image_catalog=image_catalog)
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