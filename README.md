# 課堂錄音自動化 ASR 系統 (Lecture Pipeline)

這是一套針對長度 2~3 小時大學課堂錄音設計的自動化語音辨識、內容整理與重點總結系統。
包含：
- 音訊前處理（針對 SONY ICD-PX470 桌面錄音的多開關前處理）
- 智慧 VAD 靜音切段
- 多通道 STT（Gemini 3.8 Flash, Gemini 3.5 Transcribe, 本地 Qwen3-ASR / Breeze-ASR 等）
- 字數守恆的內文整理與斷句
- 基於高星開源 Prompt 庫（Fabric）的重點結構化筆記
- 半自動同步至 Google Drive & Gemini Notebook (NotebookLM)
