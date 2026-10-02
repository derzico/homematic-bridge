FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app
ARG BRIDGE_BUILD_SHA=unknown
ENV BRIDGE_BUILD_SHA=$BRIDGE_BUILD_SHA

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python", "main.py"]
