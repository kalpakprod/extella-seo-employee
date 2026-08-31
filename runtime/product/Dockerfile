FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

RUN groupadd --gid 10001 extella \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent extella

WORKDIR /opt/extella-seo-employee
COPY --chown=10001:10001 experts ./experts
COPY --chown=10001:10001 runtime/source_proxy.py ./runtime/source_proxy.py
COPY --chown=10001:10001 runtime/container ./runtime/container
COPY --chown=0:0 runtime/product/bootstrap.py ./runtime/product/bootstrap.py
COPY --chown=10001:10001 runtime/product/gateway.py ./runtime/product/gateway.py
COPY --chown=10001:10001 runtime/product/server.py ./runtime/product/server.py
RUN mkdir -p config state reports history evidence \
    && chown -R 10001:10001 config state reports history evidence \
    && chmod 0555 runtime/container/run_crawlseo runtime/container/run_seomator

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EXTELLA_CRAWLSEO_EXECUTABLE=/opt/extella-seo-employee/runtime/container/run_crawlseo \
    EXTELLA_SEOMATOR_EXECUTABLE=/opt/extella-seo-employee/runtime/container/run_seomator \
    EXTELLA_CRAWLSEO_URL=http://crawlseo:8081/run \
    EXTELLA_SEOMATOR_URL=http://seomator:8082/run \
    EXTELLA_AGENT_ZERO_BASE_URL=http://agent-zero:80 \
    EXTELLA_AGENT_ZERO_API_KEY_FILE=/run/secrets/agent_zero_api_key

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).read()"]
CMD ["python", "/opt/extella-seo-employee/runtime/product/bootstrap.py"]
