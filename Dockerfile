FROM deepset/haystack:base-v3.0.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    curl ca-certificates inotify-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./src ./src

EXPOSE 8000
