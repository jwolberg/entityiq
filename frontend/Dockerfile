# EntityIQ operator UI — dev server image for docker-compose full-stack
# verification (docs/RUNBOOK.md "Full stack"). Not a production build.
FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
