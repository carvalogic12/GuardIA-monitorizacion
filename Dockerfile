FROM docker:29-cli

RUN apk add --no-cache ca-certificates python3

WORKDIR /app
COPY monitor.py config.docker.json ./

ENTRYPOINT ["python3", "/app/monitor.py"]
CMD ["--config", "/app/config.docker.json", "--loop-seconds", "60"]
