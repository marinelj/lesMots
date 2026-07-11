FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

# NOTE: the container filesystem is ephemeral on Cloud Run — the word bank
# (lesmots.json) is lost on instance restart unless LESMOTS_HOME points to a
# mounted volume. See "Deploying to Cloud Run" in README.md for mounting a
# GCS bucket with --add-volume/--add-volume-mount.
ENV LESMOTS_HOME=/data

EXPOSE 8321
CMD ["lesmots", "serve"]
