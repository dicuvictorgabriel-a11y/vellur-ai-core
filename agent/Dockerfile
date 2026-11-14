# VELLUR agent workspace: an isolated container where a headless OpenCode session edits a repository snapshot,
# runs its tests and reports a diff. No production secrets, no GitHub token, no Docker socket.
# node:22-bookworm already contains git, python3, curl and build tools (no apt needed).
FROM docker.io/library/node:22-bookworm
ARG OPENCODE_VERSION=1.19.5
RUN npm install -g --no-audit --no-fund opencode-ai@${OPENCODE_VERSION} \
 && useradd -m -u 10002 -s /bin/bash agent \
 && mkdir -p /work && chown agent:agent /work
COPY runner.py /usr/local/bin/vellur-runner.py
USER 10002
WORKDIR /work
ENV AGENT_WORK=/work NO_UPDATE_NOTIFIER=1
EXPOSE 7070
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request,os,sys; r=urllib.request.Request('http://127.0.0.1:7070/healthz', headers={'x-runner-secret': os.environ.get('AGENT_RUNNER_SECRET','')}); sys.exit(0 if urllib.request.urlopen(r, timeout=4).status == 200 else 1)"
CMD ["python3", "/usr/local/bin/vellur-runner.py"]
