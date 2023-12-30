FROM debian:bookworm-slim

RUN set -ex \
	&& apt-get update \
	&& apt-get -y install python3 python3-pip python3-venv \
	&& python3 -m venv /venv \
	&& /venv/bin/pip3 install "python-telegram-bot[http2]" requests thefuzz \
	&& true

ARG FIREFLY_URL
ARG FIREFLY_TOKEN
ARG FIREFLY_SOURCE_ACCOUNT
ARG TELEGRAM_BOT_TOKEN
ARG TELEGRAM_ALLOW_USERID

COPY firefly_oneshot_bot.py /
ENTRYPOINT [ "/venv/bin/python3", "/firefly_oneshot_bot.py" ]
