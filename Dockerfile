FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE requirements.lock ./
COPY approval_lab ./approval_lab
RUN pip install --no-cache-dir -r requirements.lock . && useradd --create-home lab && mkdir /app/data && chown lab:lab /app/data
USER lab
ENV APPROVAL_LAB_DATA_DIR=/app/data
EXPOSE 8765
# Container may bind its network interface; compose publishes only on host loopback.
CMD ["uvicorn", "approval_lab.app:app", "--host", "0.0.0.0", "--port", "8765", "--no-access-log"]
