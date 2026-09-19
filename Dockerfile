FROM teddysun/xray:latest AS xray

FROM alpine:3.20
RUN apk add --no-cache bash ca-certificates \
 && mkdir -p /web \
 && echo '<!doctype html><html><head><meta charset="utf-8"><title>Welcome</title></head><body style="font-family:sans-serif;text-align:center;margin-top:20vh"><h1>It works!</h1></body></html>' > /web/index.html
COPY --from=xray /usr/bin/xray /usr/local/bin/xray
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /usr/local/bin/xray
ENTRYPOINT ["/entrypoint.sh"]
