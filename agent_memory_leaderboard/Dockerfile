FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock requirements-experiment.lock ./
ARG INSTALL_EXPERIMENT_DEPS=false
RUN if [ "$INSTALL_EXPERIMENT_DEPS" = "true" ]; then \
      pip install --no-cache-dir --require-hashes -r requirements-experiment.lock; \
    else \
      pip install --no-cache-dir --require-hashes -r requirements.lock; \
    fi
RUN useradd --uid 1000 --create-home adapter && mkdir /app/var && chown adapter /app/var
COPY src ./src
USER adapter
EXPOSE 8000
CMD ["uvicorn", "src.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
