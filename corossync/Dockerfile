FROM ghcr.io/home-assistant/base-python:3.13-alpine3.22
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY app /app
COPY run.sh /
RUN chmod a+x /run.sh
CMD ["/run.sh"]
