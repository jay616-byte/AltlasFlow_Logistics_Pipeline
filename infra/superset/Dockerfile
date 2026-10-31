FROM apache/superset:6.1.0
USER root
# Superset needs the Trino DB-API driver and its SQLAlchemy dialect to query the Iceberg catalog.
RUN . /app/.venv/bin/activate && uv pip install "trino==0.340.0" "psycopg2-binary>=2.9.9"
COPY --chown=superset:superset superset_config.py /app/atlasflow/superset_config.py
COPY --chown=superset:superset init_superset_admin.py /app/atlasflow/init_superset_admin.py
RUN chmod 0444 /app/atlasflow/superset_config.py /app/atlasflow/init_superset_admin.py
ENV SUPERSET_CONFIG_PATH=/app/atlasflow/superset_config.py
USER superset
