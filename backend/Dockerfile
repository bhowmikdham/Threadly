FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /code

# Install dependencies first so code changes don't bust this layer.
COPY pyproject.toml ./
RUN python -c "import tomllib, subprocess; \
    deps = tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; \
    subprocess.check_call(['pip', 'install', *deps])"

COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

RUN useradd -m appuser && chown -R appuser /code
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
