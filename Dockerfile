FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /opt/rhexora

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir . \
    && useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin rhexora

USER 10001:10001
EXPOSE 47825/tcp 47823/udp
ENTRYPOINT ["rhexora"]
CMD ["relay-serve", "--credentials", "/run/secrets/relay-credentials.json", "--host", "0.0.0.0", "--port", "47825"]
