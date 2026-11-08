FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SRA_ROOT=/app

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install ".[google,ollama]"

COPY skills ./skills
COPY fixtures ./fixtures
COPY eval ./eval

RUN useradd --create-home sra && chown -R sra /app
USER sra

ENTRYPOINT ["sra"]
CMD ["demo"]
