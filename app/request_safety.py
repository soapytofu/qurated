"""Reject oversized requests and cross-origin browser writes before parsing bodies."""
from urllib.parse import urlsplit
from starlette.responses import JSONResponse


class RequestSafetyMiddleware:
    def __init__(self, app, max_bytes=32768):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        origin = headers.get(b'origin')
        host = headers.get(b'host', b'').decode('latin1')
        try:
            parsed = urlsplit(origin.decode('latin1')) if origin else None
            invalid_origin = parsed and (parsed.netloc != host or parsed.scheme not in {'http', 'https'})
        except ValueError:
            invalid_origin = True
        if headers.get(b'sec-fetch-site') == b'cross-site' or invalid_origin:
            return await JSONResponse({'detail': 'Cross-origin requests are not allowed.'}, 403)(scope, receive, send)
        body = bytearray()
        while True:
            event = await receive()
            if event['type'] == 'http.disconnect':
                return
            body.extend(event.get('body', b''))
            if len(body) > self.max_bytes:
                return await JSONResponse({'detail': 'Request is too large.'}, 413)(scope, receive, send)
            if not event.get('more_body', False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()

        await self.app(scope, bounded_receive, send)
