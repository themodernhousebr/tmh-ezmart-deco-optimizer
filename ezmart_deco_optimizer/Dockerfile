FROM python:3.14-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY run.py diagnostics.py tmp_readonly.py m9_actions.py ha_control.py mqtt_bridge.py x50_tpap.py x50_backend.py guest_wifi.py m5_profile.py mesh.py ip_monitor.py ./
LABEL io.hass.name="EZmart Deco Optimizer" \
      io.hass.description="Deco on-demand optimization and Home Assistant MQTT entities" \
      io.hass.type="addon" \
      io.hass.version="0.12.1"
CMD ["python", "-u", "/app/run.py"]
