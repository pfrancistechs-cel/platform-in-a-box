FROM python:3.12-slim
WORKDIR /srv
COPY router.py console.py traffic.py ./
USER 65534
EXPOSE 8080 8081
CMD ["python", "router.py"]
