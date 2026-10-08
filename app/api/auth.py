import hmac

from fastapi import Header, HTTPException, Request


def require_token(request: Request, x_access_token: str | None = Header(default=None)) -> None:
    expected = request.app.state.settings.demo_access_token
    if expected and not hmac.compare_digest((x_access_token or "").encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="access token required")
