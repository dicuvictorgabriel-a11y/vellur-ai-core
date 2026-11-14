# VELLUR AI CORE — GPU Autopilot controller. Small: Python + 4 pinned libraries, no system packages.
FROM docker.io/library/python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt \
 && useradd -r -u 10001 -d /data -s /usr/sbin/nologin autopilot \
 && mkdir -p /data && chown 10001:10001 /data
COPY app /app/app
WORKDIR /app
USER 10001
EXPOSE 8000 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"
CMD ["python", "-m", "app.main"]
