# The golden-path Dockerfile. The platform's policy (stage 2) checks it: pinned base image, non-root user, no secrets, a healthcheck.
FROM python:3.12-slim
ARG APP_VERSION=dev
ARG BUG=""
ARG REQS=requirements.txt
ENV APP_VERSION=${APP_VERSION} BUG=${BUG} PYTHONUNBUFFERED=1
WORKDIR /app
COPY ${REQS} /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY app.py .
USER 65534
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["python", "app.py"]
