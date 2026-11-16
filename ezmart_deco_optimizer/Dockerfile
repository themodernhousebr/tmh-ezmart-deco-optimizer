FROM python:3.14-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY run.py diagnostics.py tmp_readonly.py m9_actions.py ha_control.py mqtt_bridge.py x50_tpap.py x50_backend.py guest_wifi.py ./
LABEL io.hass.name="EZmart Deco Optimizer" \
      io.hass.description="Deco on-demand optimization and Home Assistant MQTT entities" \
      io.hass.type="addon" \
      io.hass.version="0.10.0-dev.3"
CMD ["python", "-u", "/app/run.py"]
