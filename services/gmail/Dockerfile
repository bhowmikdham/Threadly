FROM python:3.12-slim
WORKDIR /srv
COPY libs/common /srv/libs/common
COPY services/gmail/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt ./libs/common
COPY services/gmail/app /srv/app
ENV PORT=8030
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
