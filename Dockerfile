FROM teddysun/xray:latest AS xray

FROM alpine:3.20
RUN apk add --no-cache python3 nginx ca-certificates tzdata
COPY --from=xray /usr/bin/xray /usr/local/bin/xray
RUN chmod +x /usr/local/bin/xray && mkdir -p /app/public /tmp/nginx
WORKDIR /app
COPY panel.py /app/panel.py
COPY panel.html /app/panel.html
COPY index.html /app/public/index.html
CMD ["python3", "-u", "/app/panel.py"]
