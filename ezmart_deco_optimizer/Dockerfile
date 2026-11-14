FROM python:3.14-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY run.py ./
LABEL io.hass.name="EZmart Deco Optimizer" \
      io.hass.description="Read-only Deco diagnostics" \
      io.hass.type="addon" \
      io.hass.version="0.2.0-dev.6"
CMD ["python", "-u", "/app/run.py"]
