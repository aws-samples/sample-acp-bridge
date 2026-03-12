"""Security middleware — IP whitelist + Bearer token."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

NO_AUTH_PATHS = {"/health"}


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, allowed_ips: list[str], auth_token: str = ""):
        super().__init__(app)
        self.allowed_ips = set(allowed_ips)
        self.auth_token = auth_token

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else None

        if self.allowed_ips and client_ip not in self.allowed_ips:
            return JSONResponse({"error": "forbidden"}, status_code=403)

        if self.auth_token and request.url.path not in NO_AUTH_PATHS:
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {self.auth_token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)

        return await call_next(request)
