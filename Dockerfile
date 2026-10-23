FROM python:3.13-slim@sha256:57b29a7c050c4e679e4bea572e14b3965cf4b5839de2da49d55e49ffc4024f39

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    HOME=/tmp

# Optional CA bundle for builds behind an HTTPS proxy. Normal builds use system CAs.
RUN --mount=type=secret,id=build_ca \
    ca=/etc/ssl/certs/ca-certificates.crt; \
    if [ -s /run/secrets/build_ca ]; then ca=/run/secrets/build_ca; fi; \
    apt-get -o Acquire::https::CaInfo="$ca" update \
    && apt-get -o Acquire::https::CaInfo="$ca" upgrade -y \
    && apt-get -o Acquire::https::CaInfo="$ca" install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock.txt /app/requirements.lock.txt
RUN --mount=type=secret,id=build_ca \
    ca=/etc/ssl/certs/ca-certificates.crt; \
    if [ -s /run/secrets/build_ca ]; then ca=/run/secrets/build_ca; fi; \
    PIP_CERT="$ca" python -m pip install --no-cache-dir --disable-pip-version-check \
      --index-url https://pypi.org/simple --require-hashes --only-binary=:all: \
      -r /app/requirements.lock.txt \
    && python -m pip check \
    && python -m pip uninstall --yes pip \
    && python -c 'import shutil, sysconfig; shutil.rmtree(sysconfig.get_path("stdlib") + "/ensurepip")' \
    && groupadd --gid 1000 fava \
    && useradd --uid 1000 --gid fava --no-create-home --home-dir /tmp fava

USER 1000:1000
WORKDIR /data
EXPOSE 5000
CMD ["fava", "--host", "0.0.0.0", "--port", "5000", "/data/main.beancount"]
