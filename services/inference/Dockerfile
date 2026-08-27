FROM python:3.12-slim
WORKDIR /srv
COPY libs/common /srv/libs/common
COPY services/inference/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt ./libs/common
# BERT support is opt-in (torch is ~2GB): docker build --build-arg WITH_BERT=1
ARG WITH_BERT=0
COPY services/inference/requirements-bert.txt /srv/requirements-bert.txt
RUN if [ "$WITH_BERT" = "1" ]; then pip install --no-cache-dir -r requirements-bert.txt; fi
COPY services/inference/app /srv/app
ENV PORT=8020
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
