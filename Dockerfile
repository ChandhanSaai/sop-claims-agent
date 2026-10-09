FROM public.ecr.aws/docker/library/python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir --prefix=/install .

FROM public.ecr.aws/docker/library/python:3.12-slim
RUN useradd -m appuser && mkdir -p /app/traces && chown appuser /app/traces
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app ./app
COPY ui ./ui
COPY fixtures ./fixtures
USER appuser
ENV PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import os,sys,urllib.request; port=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+port+'/healthz').status==200 else 1)"
CMD ["sh", "-c", "uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT}"]
