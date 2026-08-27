FROM python:3.14-slim

RUN pip install --no-cache-dir \
	pymobiledevice3 \
	fastapi[standard]

WORKDIR /app
ENV PORT=80

CMD ["python3", "-m", "backend.main"]