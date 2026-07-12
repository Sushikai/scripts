# Dockerfile for the relay — works on Fly.io, Render, HF Spaces, Koyeb, etc.
FROM node:20-alpine
WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev
COPY relay_node.js ./
EXPOSE 8080
ENV PORT=8080
CMD ["node", "relay_node.js"]
