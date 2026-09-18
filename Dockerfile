FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY src/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --no-compile -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && groupadd --gid 10001 notifier \
    && useradd --uid 10001 --gid notifier --no-create-home \
       --home-dir /nonexistent --shell /usr/sbin/nologin notifier

COPY --chown=notifier:notifier src/ /app/src/
RUN mkdir /app/data && chown notifier:notifier /app/data

USER notifier:notifier

EXPOSE 8787
STOPSIGNAL SIGINT

CMD ["python", "-u", "src/server.py"]
