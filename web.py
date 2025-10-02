from flask import Flask
import threading
import time
import requests
import os

app = Flask(__name__)

@app.route('/')
def home():
    return 'Hanime Telegram Bot is running!'

def keep_alive():
    """Periodically ping the app to prevent sleeping."""
    url = os.getenv('RENDER_EXTERNAL_URL')  # Render sets this
    if url:
        while True:
            try:
                requests.get(url)
                print("Pinged self to stay awake")
            except Exception as e:
                print(f"Ping error: {e}")
            time.sleep(600)  # Ping every 10 minutes

if __name__ == '__main__':
    # Start keep-alive thread
    threading.Thread(target=keep_alive, daemon=True).start()
    # Run Flask app with Gunicorn-compatible settings
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))