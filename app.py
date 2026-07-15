"""Render entry point: keeps a small health page alive alongside the Telegram bot."""
import os
import threading

from flask import Flask
import bot

app = Flask(__name__)


@app.get("/")
def health():
    return "MADES B2B Telegram bot is running", 200


def run_bot():
    bot.main()


if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
