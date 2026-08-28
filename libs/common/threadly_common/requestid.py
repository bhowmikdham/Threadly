"""Request-ID propagation: the gateway mints (or accepts) X-Request-ID and
every hop carries it, so one chat turn is greppable across all services."""
import uuid

HEADER = "X-Request-ID"


def install_request_id(app) -> None:
    @app.middleware("http")
    async def request_id_middleware(request, call_next):
        rid = request.headers.get(HEADER) or str(uuid.uuid4())
        request.state.request_id = rid
        response = await call_next(request)
        response.headers[HEADER] = rid
        return response
