FROM python:3.12-slim

WORKDIR /app
COPY server/mcp-server/server.py server.py

ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=8080 \
    NETEASE_TZ_OFFSET=8 \
    PYTHONUNBUFFERED=1

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ['MCP_PORT']+'/health',timeout=2)"

CMD ["python", "server.py"]
