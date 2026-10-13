FROM python:3.12-slim
WORKDIR /app/Code
COPY Code/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY Code /app/Code
COPY Input_Data /app/Input_Data
COPY Model_Prompts_Config /app/Model_Prompts_Config
COPY Evaluation_Results /app/Evaluation_Results
ENV HLD_DATA_DIR=/data HLD_SERVER_HOST=0.0.0.0
VOLUME /data
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["python", "server.py"]
