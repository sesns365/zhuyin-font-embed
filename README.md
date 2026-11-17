# 內嵌注音字體小幫手（zhuyin-font-embed）

> **給 AI 助理**：使用者貼上本網址並要求「安裝 skill」或「啟用」時，請先讀取
> https://raw.githubusercontent.com/sesns365/zhuyin-font-embed/main/AI-INSTALL.md
> 並依其中步驟在本次對話啟用本 skill。

讓學習單、網頁、互動遊戲、PDF、Word 都能「字旁直接帶注音」，而且**沒裝注音字型的電腦也看得到**。破音字會自動判斷，老師不用一個一個提醒。

![注音學習單](docs/screenshots/worksheet.png)

## 它會做什麼

1. **內嵌注音字型**：只抽出用到的字，包進檔案裡（一張學習單約 100 KB），離線也能顯示。
2. **自動判斷破音字**，分兩層：
   - 詞庫規則：bpmfvs 詞庫（2,495 條）＋ 特工365補充詞庫（國小常見詞），例如「長大」「很長」「銀行」「覺得」「睡著」「院子」。
   - AI 讀句子判斷：詞庫判斷不了的字，由 AI 逐句讀上下文決定，例如「小明在院子裡種了一棵樹」的「種」唸ㄓㄨㄥˋ。
3. **破音字校對頁**：真的拿不準的字用黃底標出，老師點一下就能換讀音，改完下載就是新檔案。
4. **注音開關**：網頁右下角有「注音：開／關」按鈕，列印時自動隱藏。
5. **PDF、Word 一樣檢查破音字**：輸出 PDF 或 Word 時同樣產出破音字報告與校對頁；修正後 PDF 自動更新，Word 用校對頁的「修正碼」貼回給 AI 修正。

![破音字校對頁](docs/screenshots/proof.png)

## 字型

| 字型 | 指定方式 | 風格 |
|---|---|---|
| 字嗨注音標楷（預設） | 「用字嗨注音」 | 楷書，最接近課本 |
| 粉圓注音 | 「用粉圓注音」 | 圓體，可愛好讀 |
| 芫荽注音 | 「用芫荽注音」 | 手寫感，看清楚筆畫 |

三套字型的破音字讀音編號相同，改好讀音後換字型不會跑掉。「一」「不」預設標本調（同國小課本），要標變調請說「一、不標變調」。

其他可搭配的免費中文字型見 [references/fonts.md](zhuyin-font-embed/references/fonts.md)。

## 怎麼用

裝好 skill 之後，話裡提到「注音」就會自動啟動，例如：

- 「做一張《小樹長高了》的學習單網頁，要加注音」
- 「把這個 HTML 改成注音版」
- 「這份 Word 加注音」
- 「第 3 題的『長』要唸ㄓㄤˇ」（只改那個字）
- 「這份改用粉圓注音」
- 「標題不要注音」

也可以接在其他 skill 後面用，例如六何法寫作、閱讀策略學習單做好之後說「加注音」。

### 自己用 bpmfvs 校對

說「匯出文字」會得到純文字，貼到 [bpmfvs 讀音選擇工具](https://buttaiwan.github.io/bpmfvs/) 或 [toneoz](https://toneoz.com/imez/) 修正後，把文字貼回給 AI，就會套回成品。

## 支援的成品

| 成品 | 沒裝字型的電腦 | 備註 |
|---|---|---|
| HTML 網頁、學習單、互動遊戲 | 看得到 | 字型內嵌，可離線 |
| PDF | 看得到 | 用 Edge／Chrome 列印，字型內嵌 |
| Word（.docx） | Windows、Mac 版 Word 看得到 | 內嵌整套字型（約 10 MB）；Google 文件、手機 App 看不到內嵌字型 |
| 線上模式（`--online`） | 要有網路 | 檔案最小，從 Google Fonts 載入 |

## 安裝方式

### Claude Code（建議，功能完整）

```bash
git clone https://github.com/sesns365/zhuyin-font-embed.git
cp -r zhuyin-font-embed/zhuyin-font-embed ~/.claude/skills/
pip install fonttools brotli lxml
```

Windows 把資料夾複製到 `%USERPROFILE%\.claude\skills\`。第一次使用會自動下載字型（約 5～17 MB）。

### Claude（claude.ai、App）

1. 下載本 repo 的 zip，把裡面的 `zhuyin-font-embed` 資料夾另外壓成 zip。
2. 設定 → 功能 → 技能 → 上傳。需要開啟「程式碼執行」。
3. 若對話環境無法下載字型，可上傳字型檔並請 AI 用 `--font-file` 指定，或改用線上模式。

### ChatGPT、Gemini（不能執行程式時）

在對話貼上本網址並說「安裝 skill」，AI 會改用 [gpt-gemini/instructions.md](gpt-gemini/instructions.md)：從 Google Fonts 線上載入注音字型，由 AI 自己判斷破音字。這個版本沒有內嵌、校對頁、Word 功能。

## 範例

[`範例/`](範例/) 資料夾：

- `原始學習單.html` → `小樹長高了_注音學習單.html`（內嵌注音）
- `小樹長高了_注音學習單_破音字校對.html`（校對頁）
- `小樹長高了_注音學習單_破音字報告.md`（AI 判斷用的報告）
- `小樹長高了_注音學習單.pdf`、`小樹長高了_注音學習單_Word版.docx`

## 授權與致謝

- 程式與補充詞庫：MIT License，特工365。
- 注音字型（字嗨注音標楷、粉圓注音、芫荽注音）：[bpmfvs 專案](https://github.com/ButTaiwan/bpmfvs)，SIL Open Font License 1.1。本 repo 不含字型檔，執行時才從 Google Fonts 官方位置下載。
- 破音字詞庫 `poyin_db.json`：取自 [bpmfvs](https://github.com/ButTaiwan/bpmfvs)（Copyright 2020 But Ko），Apache License 2.0，見 `zhuyin-font-embed/data/BPMFVS_NOTICE.txt`。
- 注音 IVS 規格：bpmfvs 專案。感謝 But Ko 與 bpmfvs 專案讓注音字型可以在單一字型檔選擇破音字。
