FROM nginx:1.27-alpine

RUN apk add --no-cache openssl

COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY nginx/10-generate-self-signed-cert.sh /docker-entrypoint.d/10-generate-self-signed-cert.sh

RUN chmod +x /docker-entrypoint.d/10-generate-self-signed-cert.sh
