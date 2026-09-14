# TLS ingress runbook (reference)

Product containers publish API only on loopback (`127.0.0.1:8088`, see
`deploy/compose.yaml`). Any external access MUST terminate TLS and authenticate at a
reverse proxy on the host. Never publish `8088` (or any product port) on a public
interface, and never point the proxy at anything except `127.0.0.1:8088`.

Status 2026-09-14: reference configs below, live termination not yet proven (needs a
staging host with a domain — plan open question 3). They are written to be applied
verbatim on that host.

## Option A: Caddy (recommended, automatic ACME)

```caddyfile
seo-staging.example.com {
    reverse_proxy 127.0.0.1:8088
    header {
        # HSTS only after you confirmed HTTPS works; then keep.
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
    }
    log {
        output file /var/log/caddy/seo-access.log
        # Do not log Authorization values: Caddy's default `log` does not log
        # headers, keep it that way.
    }
}
```

## Option B: nginx

```nginx
server {
    listen 443 ssl;
    server_name seo-staging.example.com;
    ssl_certificate /etc/letsencrypt/live/seo-staging.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/seo-staging.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    location / {
        proxy_pass http://127.0.0.1:8088;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        # Authorization passes through untouched; product validates the Bearer token.
    }
    access_log /var/log/nginx/seo-access.log combined;
}
server {
    listen 80;
    server_name seo-staging.example.com;
    return 301 https://$host$request_uri;
}
```

## Checks after applying

```sh
# 1. Plain HTTP redirects, HTTPS serves, loopback-only origin is intact:
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://seo-staging.example.com/health
curl -s https://seo-staging.example.com/health  # {"status":"ok","version":...}
ss -ltn | grep 8088  # must show 127.0.0.1:8088 only

# 2. Auth still enforced at the product (proxy adds no auth bypass):
curl -s -o /dev/null -w '%{http_code}\n' https://seo-staging.example.com/api/state  # 401/400, never 200 without token

# 3. Rate limit visible through the proxy (120/min/IP default):
for i in $(seq 1 130); do curl -s -o /dev/null -w '%{http_code}\n' https://seo-staging.example.com/health; done | sort | uniq -c
# expect mostly 200 with a 429 tail and a Retry-After header

# 4. No secret values in proxy logs:
grep -ic 'authorization' /var/log/caddy/seo-access.log /var/log/nginx/seo-access.log  # expect 0
```

## Rotation interaction

Token rotation (`prepare.py --rotate-secret` + container restart) is independent of the
proxy: the proxy never stores the token. After rotation, old clients get 401 from the
product through the proxy — verify with the same check 2 using the old token.
