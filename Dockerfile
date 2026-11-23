# Image ghcr.io/rheckly/immich-puzzle, built by .github/workflows/release.yml for every tag vX.Y.Z.
# Run it with compose.yaml (see README.md).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=UTC
WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt
COPY app/ .
RUN find . -name '__pycache__' -prune -exec rm -rf {} +
EXPOSE 8080 8081 8082
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=4)"
CMD ["python", "-u", "server.py"]
