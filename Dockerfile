# One image for Fava and the jobs next to it. Dependencies are pinned in requirements.lock.
FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

# git and ssh push the backup; libmagic is needed by beangulp, which Fava imports.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client libmagic1 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    LEDGER_FILE=/data/main.beancount

WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY jobs ./jobs

# The containers run as the owner of the data directory (LEDGER_UID/LEDGER_GID in compose.yaml).
# ssh refuses to start for a uid without a passwd entry, so that uid gets one, with /tmp as home.
ARG LEDGER_UID=1000
ARG LEDGER_GID=1000
RUN (getent group "$LEDGER_GID" >/dev/null || groupadd --gid "$LEDGER_GID" ledger) \
    && (getent passwd "$LEDGER_UID" >/dev/null || useradd --uid "$LEDGER_UID" --gid "$LEDGER_GID" --no-create-home --home-dir /tmp --shell /usr/sbin/nologin ledger)

USER ${LEDGER_UID}:${LEDGER_GID}
EXPOSE 5000
CMD ["sh", "-c", "exec fava --host 0.0.0.0 --port 5000 \"$LEDGER_FILE\""]
