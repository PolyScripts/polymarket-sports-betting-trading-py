# Polymarket 體育博彩機器人 – 即時體育一鍵下注

**聯繫方式：** [Telegram @movez_x](https://t.me/movez_x)

[English](README.md) | 中文

---

## 這是什麼機器人？

這款機器人幫助 Polymarket 上的體育博彩交易者，提供快速、專注的即時體育市場介面。無需在官網層層點擊，你就能看到足球、籃球、冰球、網球等即時賽事的清晰網格與即時價格，且不含電競雜訊。你點擊，機器人會在 **~20ms** 內以市價單（FAK）下單，讓你在賠率變動時快速反應。這是手動點擊下注：你決定每一筆，機器人負責執行。使用 Rust 開發。

---

## 截圖

![截圖 1](images/1.png)
![截圖 2](images/2.png)
![截圖 3](images/3.png)
![截圖 4](images/4.png)

---

## 三大交易者優勢

1. **速度即優勢** – 約 20ms 內下單。即時體育博彩中賠率變化很快，執行越快，鎖定的價格越好，利潤越不容易被滑點吃掉。
2. **即時價格，無延遲** – 透過 WebSocket 即時更新，下注前看到的是真實市場。沒有過時價格，沒有整頁刷新延遲，按實際行情交易。
3. **你的優勢，你來掌控** – 沒有黑箱自動化。你發現價值，你點擊，機器人執行。保留你的優勢與判斷力，適合懂體育的交易者。

---

## 新增功能、尋求幫助或獲取進階版

請透過 **Telegram: [@movez_x](https://t.me/movez_x)** 聯繫

---

## 如何運行

1. 複製 `.env.example` 為 `.env` 並填入你的錢包：

   ```bash
   cp .env.example .env
   ```

2. 安裝依賴：

   ```bash
   pip install -r requirements.txt
   ```

3. 啟動機器人：

   ```bash
   python sports_server.py
   ```

4. 在瀏覽器中打開 **http://localhost:5050**。

---

## 聯繫方式

**Telegram：** [@movez_x](https://t.me/movez_x)
