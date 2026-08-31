FROM python:3.13-slim

RUN pip install --no-cache-dir \
	pymobiledevice3

WORKDIR /app
ENV PORT=80

CMD ["uvicorn", "backend.api:app", "--host", "0.0.0.0"]