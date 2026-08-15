FROM python:3.11-slim

# System deps: poppler/tesseract for OCR fallback paths, ODBC driver deps for MSSQL,
# and OpenCV runtime libs.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gnupg2 unixodbc-dev tesseract-ocr libgl1 libglib2.0-0 \
    && curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
    && curl https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# Note: torch/torch-geometric/ultralytics/detectron2 are heavy. If you don't
# need GNN refinement or trained CV models, trim requirements.txt before
# building to keep the image small.
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/storage/uploads /app/storage/crops /app/models_weights

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
