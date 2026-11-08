FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/rhexora
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir '.[ice]' \
    && useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin rhexora
USER 10001:10001
EXPOSE 47826/tcp
ENTRYPOINT ["rhexora"]
CMD ["ice-signal-serve", "--host", "0.0.0.0", "--port", "47826"]
