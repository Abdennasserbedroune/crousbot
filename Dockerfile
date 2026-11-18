FROM python:3.12-slim
WORKDIR /app
COPY crous_bot.py .
CMD ["python", "-u", "crous_bot.py"]
