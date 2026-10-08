ARG THERMAL_BASE_IMAGE=python:3.12-slim-bookworm
FROM ${THERMAL_BASE_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 THERMAL_DATA_DIR=/data
RUN if [ "$(dpkg-query -W -f='${Status}\n' libgl1 libglu1-mesa libxrender1 libxext6 libgomp1 2>/dev/null | grep -c '^install ok installed$')" != 5 ]; then apt-get update && apt-get install -y --no-install-recommends libgl1 libglu1-mesa libxrender1 libxext6 libgomp1 && rm -rf /var/lib/apt/lists/*; fi
WORKDIR /app
COPY requirements.txt /app/requirements.txt
ARG THERMAL_PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --timeout 120 --index-url "$THERMAL_PIP_INDEX_URL" -r requirements.txt
COPY thermal_service/ /app/thermal_service/
COPY vendor/ /app/vendor/
RUN useradd --create-home --uid 10001 thermal && mkdir -p /data && chown thermal:thermal /data
USER thermal
VOLUME ["/data"]
EXPOSE 8790
CMD ["uvicorn", "thermal_service.app:app", "--host", "0.0.0.0", "--port", "8790", "--workers", "1"]
