FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    libgeos-c1v5 \
    && rm -rf /var/lib/apt/lists/*

ARG DEPTHMAPX_VERSION=0.8.0
RUN curl -fsSL \
    "https://github.com/SpaceGroupUCL/depthmapX/releases/download/v${DEPTHMAPX_VERSION}/depthmapXcli_linux64" \
    -o /usr/local/bin/depthmapXcli \
    && chmod +x /usr/local/bin/depthmapXcli

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data
COPY tools ./tools
# Adaptive industry profiles are read from disk at request time
# (app/services/industry_profiles.py globs this tree). Without it the API
# serves an empty profile list and every lookup 404s.
COPY industry-profiles ./industry-profiles

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
