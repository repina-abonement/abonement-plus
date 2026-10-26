FROM python:3.12-slim
WORKDIR /app
COPY crm ./crm
ENV CRM_HOST=0.0.0.0 CRM_PORT=8000 CRM_DB=/data/crm.sqlite3 PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "crm.server"]
