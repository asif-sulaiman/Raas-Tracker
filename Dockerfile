# ---- Frontend build ----
FROM node:22-slim AS frontend
WORKDIR /app/chemcalc-frontend
COPY chemcalc-frontend/package.json chemcalc-frontend/package-lock.json ./
RUN npm ci
COPY chemcalc-frontend/ ./
RUN npx vite build

# ---- Backend ----
FROM python:3.10-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PRODUCTION=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY flask_app.py wsgi.py chem_stock.py parse_sales.py parse_stock.py ./
COPY chemcalc/ ./chemcalc/
COPY --from=frontend /app/chemcalc-frontend/dist ./react_frontend
VOLUME ["/data"]
ENV CHEMCALC_DATA_DIR=/data
EXPOSE 5000
CMD ["waitress-serve", "--host=0.0.0.0", "--port=5000", "--threads=4", "wsgi:app"]
