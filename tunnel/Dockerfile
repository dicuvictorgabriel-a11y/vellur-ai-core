# VELLUR AI CORE — outbound SSH tunnel to the GPU workspace (HyperAI, or any SSH-reachable GPU host).
# The VPS opens the connection; the GPU side never receives any credential for the VPS.
# Built on the VPS by `vac gpu up` (needs only the official alpine image and its package mirror).
FROM docker.io/library/alpine:3.22
RUN apk add --no-cache openssh-client \
 && adduser -D -H -u 10001 tunnel
COPY tunnel.sh /usr/local/bin/tunnel.sh
USER 10001
ENTRYPOINT ["/bin/sh", "/usr/local/bin/tunnel.sh"]
