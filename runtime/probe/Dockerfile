FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8085

WORKDIR /app
COPY --chown=10001:10001 runtime/probe/entrypoint.py /app/entrypoint.py
RUN groupadd --gid 10001 extella \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent extella

USER 10001:10001
EXPOSE 8085 8086 8087
ENTRYPOINT ["python", "/app/entrypoint.py"]
