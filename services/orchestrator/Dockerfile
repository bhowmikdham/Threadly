FROM python:3.12-slim
WORKDIR /srv
COPY libs/common /srv/libs/common
COPY services/orchestrator/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt ./libs/common
COPY services/orchestrator/app /srv/app
ENV PORT=8010
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
