import httpx

# One shared client; SSE streams need no read timeout.
http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))
