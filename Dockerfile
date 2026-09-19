FROM teddysun/xray:latest AS xray

FROM alpine:3.20
RUN apk add --no-cache bash ca-certificates
COPY --from=xray /usr/bin/xray /usr/local/bin/xray
COPY entrypoint.sh /entrypoint.sh
COPY web /web
RUN chmod +x /entrypoint.sh /usr/local/bin/xray
ENTRYPOINT ["/entrypoint.sh"]
