FROM python:3.13-slim
RUN apt-get update \
	&& apt-get install -y --no-install-recommends build-essential \
	&& pip install --no-cache-dir pymobiledevice3 \
	&& apt-get purge -y --auto-remove build-essential \
	&& rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PORT=80

CMD ["uvicorn", "backend.api:app", "--host", "0.0.0.0"]