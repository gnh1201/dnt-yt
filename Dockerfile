FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl unzip \
  && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -U pip && pip install --no-cache-dir yt-dlp

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# --- install Deno (>= 2.0.0) ---
RUN curl -fsSL https://deno.land/install.sh | sh

# Add Deno to PATH (avoid quoting pitfalls)
ENV DENO_INSTALL=/root/.deno
ENV PATH=/root/.deno/bin:${PATH}

# Verify Deno (use absolute path for reliability)
RUN /root/.deno/bin/deno --version

COPY app /app/app
COPY worker /app/worker

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
