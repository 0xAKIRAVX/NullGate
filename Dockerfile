FROM teddysun/xray:latest AS xray

FROM alpine:3.20
RUN apk add --no-cache python3 ca-certificates
COPY --from=xray /usr/bin/xray /usr/local/bin/xray
COPY panel.py /panel.py
RUN chmod +x /usr/local/bin/xray
CMD ["python3", "-u", "/panel.py"]
