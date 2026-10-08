FROM python:3.12-slim
WORKDIR /app
COPY server.py ./
COPY static ./static
ENV PORT=8000 DB_PATH=/data/migration.db
VOLUME /data
CMD ["python", "server.py"]
