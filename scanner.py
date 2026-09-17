import json
import os
import time
import urllib.parse
import urllib.request
from statistics import mean

BASE = "https://api.coindcx.com"

# Scanner settings
TOP_N = 40
MOVE_5M = 1.0
VOLUME_SPIKE = 2.0
RSI_LEVEL = 55.0
MIN_SCORE = 3
COOLDOWN = 30  # minutes


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "CryptoEarlyMoveScanner/1.0"}
    )

    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode())


def get_data(url, params):
    query = urllib.parse.urlencode(params)
    return get_json(url + "?" + query)


def telegram_alert(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message
    }).encode()

    request = urllib.request.Request(
        url,
        data=data,
        method="POST"
    )

    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.loads(response.read().decode())

    if not result.get("ok"):
        raise RuntimeError(result)


def rsi(closes, period=14):

    if len(closes) < period + 1:
        return 50.0

    gains = []
    losses = []

    for old, new in zip(
        closes[-period - 1:-1],
        closes[-period:]
    ):
        change = new - old

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = mean(gains)
    avg_loss = mean(losses)

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def make_5m_candles(candles):

    buckets = {}

    for candle in candles:

        timestamp = int(candle["time"])

        bucket = (
            timestamp // 300000
        ) * 300000

        buckets.setdefault(
            bucket,
            []
        ).append(candle)

    current_bucket = (
        int(time.time() * 1000) // 300000
    ) * 300000

    result = []

    for bucket, rows in buckets.items():

        if bucket >= current_bucket:
            continue

        rows.sort(
            key=lambda x: int(x["time"])
        )

        if len(rows) < 3:
            continue

        result.append({
            "time": bucket,
            "open": float(rows[0]["open"]),
            "high": max(
                float(x["high"]) for x in rows
            ),
            "low": min(
                float(x["low"]) for x in rows
            ),
            "close": float(rows[-1]["close"]),
            "volume": sum(
                float(x["volume"]) for x in rows
            )
        })

    result.sort(
        key=lambda x: x["time"]
    )

    return result


def main():

    print("Starting crypto scanner...")

    details = get_json(
        BASE + "/exchange/v1/markets_details"
    )

    tickers = get_json(
        BASE + "/exchange/ticker"
    )

    ticker_map = {
        str(x.get("market")): x
        for x in tickers
    }

    markets = []

    # Select active USDT spot markets
    for detail in details:

        if detail.get("status") != "active":
            continue

        if (
            detail.get(
                "base_currency_short_name"
            ) != "USDT"
        ):
            continue

        market = detail.get(
            "coindcx_name"
        )

        ticker = ticker_map.get(market)

        if not ticker:
            continue

        try:

            price = float(
                ticker["last_price"]
            )

            volume = float(
                ticker["volume"]
            )

            turnover = price * volume

            markets.append(
                (turnover, detail)
            )

        except (KeyError, ValueError, TypeError):
            continue

    # Highest turnover markets first
    markets.sort(
        key=lambda x: x[0],
        reverse=True
    )

    alerts = []

    for _, detail in markets[:TOP_N]:

        pair = detail.get("pair")

        if not pair:
            continue

        try:

            candles = get_data(
                BASE + "/market_data/candles",
                {
                    "pair": pair,
                    "interval": "1m",
                    "limit": 70
                }
            )

            candles_5m = make_5m_candles(
                candles
            )

            if len(candles_5m) < 16:
                continue

            current = candles_5m[-1]

            previous = candles_5m[-13:-1]

            # 5-minute price move
            price_move = (
                (
                    current["close"]
                    / current["open"]
                ) - 1
            ) * 100

            # Average previous 12 candle volume
            average_volume = mean(
                x["volume"]
                for x in previous
            )

            if average_volume <= 0:
                continue

            volume_ratio = (
                current["volume"]
                / average_volume
            )

            # Previous resistance
            previous_high = max(
                x["high"]
                for x in previous
            )

            breakout = (
                current["close"]
                > previous_high
            )

            closes = [
                x["close"]
                for x in candles_5m
            ]

            current_rsi = rsi(
                closes,
                14
            )

            score = 0

            if price_move >= MOVE_5M:
                score += 1

            if volume_ratio >= VOLUME_SPIKE:
                score += 1

            if breakout:
                score += 1

            if current_rsi >= RSI_LEVEL:
                score += 1

            if score < MIN_SCORE:
                continue

            alerts.append({
                "symbol": detail.get(
                    "target_currency_short_name",
                    "UNKNOWN"
                ),
                "pair": pair,
                "price": current["close"],
                "move": price_move,
                "volume": volume_ratio,
                "breakout": breakout,
                "rsi": current_rsi,
                "score": score
            })

        except Exception as error:

            print(
                "Skipping",
                pair,
                error
            )

    # Strongest signals first
    alerts.sort(
        key=lambda x: (
            x["score"],
            x["move"],
            x["volume"]
        ),
        reverse=True
    )

    print(
        "Signals found:",
        len(alerts)
    )

    # Send maximum 5 alerts
    for alert in alerts[:5]:

        message = (
            "🚨 EARLY MOVE ALERT\n\n"

            f"Coin: "
            f"{alert['symbol']}/USDT\n"

            f"Price: "
            f"{alert['price']:.8g} USDT\n"

            f"5m Move: "
            f"{alert['move']:+.2f}%\n"

            f"Volume: "
            f"{alert['volume']:.1f}x average\n"

            f"Breakout: "
            f"{'YES' if alert['breakout'] else 'NO'}\n"

            f"RSI: "
            f"{alert['rsi']:.1f}\n"

            f"Signal Score: "
            f"{alert['score']}/4\n\n"

            "Educational alert only."
        )

        telegram_alert(message)

        print(
            "Telegram alert sent:",
            alert["symbol"]
        )


if __name__ == "__main__":
    main()
