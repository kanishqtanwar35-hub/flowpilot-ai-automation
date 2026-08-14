"""Entrypoint:  python run.py"""
from app import create_app
from app.config import Config

app = create_app()

if __name__ == "__main__":
    print(f"\n  FlowPilot dashboard  ->  http://127.0.0.1:{Config.PORT}")
    print(f"  Inbound webhook      ->  POST http://127.0.0.1:{Config.PORT}/webhook/inbound")
    print(f"  Mode                 ->  {Config.summary()}\n")
    app.run(host="0.0.0.0", port=Config.PORT, debug=Config.DEBUG, threaded=True)
