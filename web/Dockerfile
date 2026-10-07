FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 3000

# Dev server with hot reload, matching the API's --reload. The /api rewrite in
# next.config.ts reads API_URL when the server starts, so compose can point it
# at the api container.
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0", "--port", "3000"]
