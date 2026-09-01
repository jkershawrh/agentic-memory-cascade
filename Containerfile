FROM registry.access.redhat.com/ubi9/python-311:latest

WORKDIR /opt/app-root/src

COPY pyproject.toml README.md LICENSE ./
COPY agentic_memory_cascade/ agentic_memory_cascade/
RUN pip install --no-cache-dir .

EXPOSE 8090
USER 1001

CMD ["uvicorn", "agentic_memory_cascade.service:app", "--host", "0.0.0.0", "--port", "8090"]
