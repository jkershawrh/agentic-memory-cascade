FROM registry.access.redhat.com/ubi9/python-311:latest

WORKDIR /opt/app-root/src

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY cascade_compression/ cascade_compression/
COPY config/ config/
COPY data/ data/
COPY frontend/ frontend/

RUN pip install --no-cache-dir ".[aap]"

EXPOSE 8090

USER 1001

CMD ["uvicorn", "cascade_compression.service:app", "--host", "0.0.0.0", "--port", "8090"]
