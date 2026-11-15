# VELLUR agent workspace: an isolated container where a headless OpenCode session edits a repository snapshot,
# runs its tests, takes real screenshots of the pages it changes and reports the changes.
# No production secrets, no GitHub token, no Docker socket.
# node:22-bookworm already contains git, python3, curl and build tools. Playwright's Chromium headless shell
# (~250 MB on disk with its system libraries, memory only while a screenshot is taken) is installed for
# `vellur-screenshot`; build with AGENT_BROWSER=0 to skip it.
FROM docker.io/library/node:22-bookworm
ARG OPENCODE_VERSION=1.19.5
ARG AGENT_BROWSER=1
ARG PLAYWRIGHT_VERSION=1.55.0
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
# The browser is optional: if its download fails the image still builds and `vellur-screenshot` explains that
# screenshots are unavailable (the agent reports it instead of inventing previews).
RUN if [ "$AGENT_BROWSER" = "1" ]; then \
      ( npx -y playwright@${PLAYWRIGHT_VERSION} install --with-deps --only-shell chromium \
        && apt-get install -y --no-install-recommends fonts-liberation fonts-noto-color-emoji ) \
      || echo "WARNING: browser not installed; screenshots will be unavailable"; \
      rm -rf /var/lib/apt/lists/* /root/.npm; fi \
 && ( apt-get update && apt-get install -y --no-install-recommends python3-venv python3-pip \
      || echo "WARNING: python venv tools not installed" ) && rm -rf /var/lib/apt/lists/* \
 && npm install -g --no-audit --no-fund opencode-ai@${OPENCODE_VERSION} playwright-core@${PLAYWRIGHT_VERSION} \
 && useradd -m -u 10002 -s /bin/bash agent \
 && mkdir -p /work && chown agent:agent /work
COPY runner.py /usr/local/bin/vellur-runner.py
COPY vellur-screenshot /usr/local/bin/vellur-screenshot
USER 10002
WORKDIR /work
ENV AGENT_WORK=/work NO_UPDATE_NOTIFIER=1
EXPOSE 7070
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request,os,sys; r=urllib.request.Request('http://127.0.0.1:7070/healthz', headers={'x-runner-secret': os.environ.get('AGENT_RUNNER_SECRET','')}); sys.exit(0 if urllib.request.urlopen(r, timeout=4).status == 200 else 1)"
CMD ["python3", "/usr/local/bin/vellur-runner.py"]
