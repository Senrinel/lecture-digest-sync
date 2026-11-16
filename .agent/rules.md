# 專案操作規範 (Project Rules)

## 1. 原始碼同步與儲存庫規範 (GitHub Remote-First)
- **拉取優先**：每次對專案進行回應、維護或修改前，必須確保自 GitHub 遠端倉庫 (`origin/main`) 拉取最新未混淆原始碼。
- **僅上傳核心未混淆源碼與腳本**：上傳至 GitHub 時，僅允許未混淆之 Python 原始碼、單元測試、設定範本與混淆/打包腳本 (`scripts/`, `build_exe.py`)。
- **嚴格禁止上傳**：
  - 機密憑證 (`.env`, `credentials.json`, `token.json` 等)。
  - 錄音媒體與音訊檔案 (`*.wav`, `*.mp3`, `*.flac`, `*.m4a` 等)。
  - 混淆生成物與編譯二進位檔案 (`build/`, `dist/`, `*.pyd`, `*.so` 等)。
  - 執行期日誌與暫存工作目錄 (`work/*`, `archive/*`, `inbox/*`, `logs/*`)。
- **操作完成即上傳**：任務修改與測試驗證完成後，必須將變更推播至遠端 GitHub 倉庫。

## 2. 本地空間極致最小化規範 (Zero Local Space Footprint)
- **本地無狀態 / 不佔用空間**：本地目錄僅作為臨時構建或執行環境。在完成建置、發布與上傳作業後，必須主動清理本地的臨時中介產物（包含 `build/`, `dist/`, `.pytest_cache/`, `__pycache__/`, 測試暫存目錄等）。
- **依賴已安裝版執行**：日常背景監控與用戶端使用均基於安裝至本機的獨立應用程式 (`%LOCALAPPDATA%\Programs\LecturePipeline`)，原始碼目錄維持乾淨輕量。

## 3. 打包與安裝規範
- 打包發布必須遵循雙層混淆機制：
  1. 第一層 (Layer A)：AST 變數與語法樹精簡混淆 (`python_minifier`)。
  2. 第二層 (Layer B)：關鍵核心模組 Cython 原生 C-Extension 編譯 (`.pyd`)。
- 透過 `build_exe.py` 與 Inno Setup 編譯成 Windows 獨立安裝程式，並自動更新安裝至用戶端目錄。
