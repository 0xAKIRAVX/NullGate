FROM alpine:3.20

ARG TARGETARCH
RUN apk add --no-cache curl unzip ca-certificates bash \
 && case "${TARGETARCH:-amd64}" in \
      amd64) A=64 ;; \
      arm64) A=arm64-v8a ;; \
      *) echo "unsupported arch"; exit 1 ;; \
    esac \
 && curl -fsSL -o /tmp/xray.zip "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-${A}.zip" \
 && unzip /tmp/xray.zip -d /usr/local/bin xray \
 && chmod +x /usr/local/bin/xray \
 && rm /tmp/xray.zip

COPY entrypoint.sh /entrypoint.sh
COPY web /web
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
