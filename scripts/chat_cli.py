"""Terminal chat against the real pipeline. Usage: python scripts/chat_cli.py [scenario]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.engine.service import build_service  # noqa: E402

svc = build_service(get_settings())
session = svc.start(sys.argv[1] if len(sys.argv) > 1 else "default")
print(f"assistant> {session.transcript[-1].text}")
while True:
    try:
        text = input("you> ").strip()
    except (EOFError, KeyboardInterrupt):
        break
    if not text:
        continue
    res = svc.chat(session, text)
    print(f"assistant> {res.reply}")
    print(f"   [phase={session.phase.value} verified={session.verification.status}"
          f" pending={session.pending_ask.value}"
          f" guard={'ok' if session.last_guard['ok'] else session.last_guard['violations']}]")
