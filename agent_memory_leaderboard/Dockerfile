FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
RUN useradd --uid 1000 --create-home adapter && mkdir /app/var && chown adapter /app/var
COPY src ./src
USER adapter
EXPOSE 8000
CMD ["uvicorn", "src.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
