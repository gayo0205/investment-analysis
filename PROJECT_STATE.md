# Project State

最後整理時間：2026-05-23 02:20，台灣時間。

## 專案定位

這是投資分析儀表板，目標是把股票、ETF、市場資料翻成小白看得懂的決策輔助頁。

本版本原則：

- 不放私人持股、成本、券商、帳戶、真實預算。
- 不放 API key、AI key、token、`.env`。
- 不接需要付費或正式授權的即時資料。
- 使用免費公開資料與固定規則。
- 只提供觀察與紀律提醒，不提供正式投資建議。

後續個人化版本才處理：

- 個人持股、成本、損益。
- 目標配置、現金水位、每月可投入金額。
- 真正判斷該不該買、賣幾股、賣哪一部分。

## 目前主要檔案

- `generate.py`：主程式，抓資料、算指標、產生 `index.html`。
- `data_sources.py`：免費公開資料源，目前包含 TWSE `STOCK_DAY_ALL`、FinMind 無 token 台股資料、台股 ETF 官方公開資料。
- `index.html`：公開展示頁。
- `.github/workflows/update-report.yml`：GitHub Actions 自動更新。
- `requirements.txt`：Actions / 本機必要套件。
- `README.md`：使用與部署說明。
- `PUBLIC_RELEASE_AUDIT.md`：資料源、限制、下一步稽核。
- `CHANGELOG.md`：功能與邏輯更新日誌。
- `.gitignore`：排除 cache、pyc、env、log。

## 時間抓取邏輯

報告產生時間：

- `generate.py` 使用 `datetime.now(ZoneInfo('Asia/Taipei'))`。
- 頁面 header 與資料檢查會顯示台灣時間。
- GitHub Actions 也設定 `TZ=Asia/Taipei`，但程式內仍明確指定時區，避免 UTC 誤差。

報價時間：

- Yahoo Finance：讀 `regularMarketTime` 與 `exchangeTimezoneName`。
- 顯示時一律轉成台灣時間；若是海外市場，再用括號補原市場時間，例如 `台灣 05/15 02:00（美東 05/14 14:00）`。
- TWSE `STOCK_DAY_ALL`：屬盤後公開資料，不是即時價。程式用該資料日的 `14:30 Asia/Taipei` 作為盤後資料時間標記，重點是資料日期與來源，不是最後一筆成交秒數。

市場狀態：

- 台股：依台灣時間 09:00 到 13:30 判斷尚未開盤、開盤中、已收盤、休市日。
- 非台股：依美東時間 09:30 到 16:00 判斷。
- TWSE 盤後資料會標示 `已收盤` 或目前實際休市狀態。

## 目前資料源

台股價格：

- 盤中：優先使用 Yahoo Finance 最新/延遲報價，避免 TWSE 盤後日資料覆蓋盤中價。
- 收盤後、休市日、開盤前：優先使用 TWSE `STOCK_DAY_ALL` 盤後公開資料。
- Yahoo Finance 作備援與歷史線圖來源。
- 有價格尺度驗證：若 TWSE 價格與 Yahoo 日線差距過大，就不合併 TWSE，避免資料單位或欄位差異造成錯誤。
- 有分割/還原價格處理：若偵測到歷史價格突然跳落或跳升，會用同一價格尺度重算 52 週位置、均線、回撤與報酬，避免 ETF 分割後誤判。

台股個股基本面與籌碼：

- FinMind 無 token 免費資料補：PE、PBR、殖利率、月營收 YoY/MoM、近四季 EPS、EPS YoY、毛利率、三大法人買賣超。
- Yahoo Finance 仍補 ROE、Forward PE、部分財務欄位。
- 若 FinMind 暫時失敗，頁面會保留 N/A 或 Yahoo 備援，不把缺資料當正常。

台股 ETF 資料：

- Yahoo Finance 補費用率、折溢價、規模、配息、成分股與歷史價格。
- 政府/公會公開資料補基金基本資料、追蹤指數、基金類型、上市日期、基金費用月與每日淨值。
- TWSE ETF e添富配息清單補近12月配息、最近配息、下次除息與配息來源組成。
- 費用資料若月份偏舊，頁面會標示「僅供參考」，且不拿來幫 ETF 評分加分。
- 若 SITCA 每日淨值無法用 ETF 代號或基金統編對上，頁面會顯示 `N/A` 與待補，不把缺資料當成正常。
- 追蹤差只在可合理對照時顯示；主題 ETF、台幣海外 ETF、指數不匹配時會顯示 N/A 與原因，不硬湊數字。

美股、美股 ETF、債券商品 ETF：

- 目前主要使用 Yahoo Finance。

題材觀察：

- 目前是靜態題材清單，不接新聞 API。
- 原則：新聞只提供關注清單，不當買進訊號。

## GitHub Actions 更新

`update-report.yml` 目前排程：

- 06:45 台灣時間：美股收盤後。
- 11:30 台灣時間：台股盤中刷新。
- 15:15 台灣時間：台股收盤後。
- 可手動 `workflow_dispatch`。

Actions 不使用 secrets、不使用 API key。

資料刷新紀錄：

- 自動資料刷新由 GitHub Actions run history 與 `Update public investment report` commit 記錄。
- 功能、邏輯、資料源調整記錄在 `CHANGELOG.md`。

## 目前功能完成度

已完成：

- 頁面產生。
- 台股個股、台股 ETF、美股個股、美股 ETF、債券/商品分類。
- 市場溫度。
- 小白決策卡：結論、原因、反方、行動。
- 資料可信度。
- 因子分數：趨勢、動能、成交量、位置、風險、基本面或 ETF 資料。
- 買進 / 減碼價格地圖。
- 今日掛單總覽：每檔標的試算買入掛單價、投入金額、約可買股數、最高買價、賣出觀察價與失效線。
- 零股試算顯示。
- 定期定額模擬器。
- 今天想買試算。
- 題材觀察雷達。
- 計算方式說明。
- 資料檢查區。
- GitHub Actions 自動更新。
- TWSE 盤後公開資料優先補台股價格。
- FinMind 無 token 補台股個股基本面與三大法人籌碼。
- 台股盤中/收盤後資料源優先順序已分開。
- 台股 ETF 已補官方公開基本資料、基金費用資料與可對上的每日淨值。
- 台股 ETF 已補 TWSE ETF e添富配息清單，顯示近12月配息、最近/下次除息與配息來源。
- 定期定額模擬器 ETF 優先用 Yahoo Adj Close 做含息估算，並在結果顯示模擬口徑。
- 首頁已移除可見的版本分工字樣，避免干擾一般使用者。
- 報告健康檢查已補：若資料抓取失敗過多，保留既有 `index.html` 不覆蓋。
- yfinance 快取改用 `.yf_cache_runtime/`，降低舊 SQLite 快取鎖住造成整批下載失敗的風險。

待補：

- 台股長期歷史資料正式化：TWSE 歷史日資料或 FinMind。
- 台股基本面下一階段：ROE 與現金流改用更正式來源，降低 Yahoo 欄位缺漏。
- ETF 下一階段：完整成分股、正式折溢價、正式追蹤誤差與更正式的含息總報酬來源。
- 籌碼下一階段：融資融券、外資持股。
- 定期定額含息再投入回測已先用 Adj Close 估算，下一步可補更正式的台股總報酬資料源。
- 債券利率環境、殖利率曲線。
- 商品 ETF 期貨轉倉成本。
- 後續個人化版本。

## 本機執行

```bash
pip install -r requirements.txt
python generate.py
```

Windows 本機若遇到中文輸出亂碼，可先設定：

```powershell
$env:PYTHONIOENCODING='utf-8'
python generate.py
```

## 重要風險與原則

- 不要把 FinMind token 寫進 repo；目前刻意使用無 token 模式。
- 不要接 TWSE 需要簽約或付費的正式即時資料。
- 不要把私人資料推到 GitHub。
- 如果資料來源回傳異常，要顯示資料不足或 fallback，不要硬湊假數字。
- 本頁不能幫使用者判斷「本人該賣幾股」，只能給市場與標的狀態。
