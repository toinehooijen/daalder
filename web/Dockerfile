FROM caddy:alpine

COPY Caddyfile /etc/caddy/Caddyfile
COPY . /srv

# Railway injects $PORT at runtime; the Caddyfile binds to it directly.
