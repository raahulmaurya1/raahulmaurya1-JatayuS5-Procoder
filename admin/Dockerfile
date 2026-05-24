# Build Stage
FROM node:20 AS builder

WORKDIR /app

# Install dependencies
# NOTE: Delete package-lock.json first — it was generated on Windows and contains
# Windows-specific native binaries (e.g. @tailwindcss/oxide). We must let npm
# resolve fresh Linux/Alpine-compatible binaries inside the container.
COPY package.json ./
RUN npm install --os=linux --cpu=x64

# Inject API Base URL during build
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

# Copy source code and build
COPY . .
RUN npm run build

# Serve Stage
FROM nginx:alpine
# Copy built assets from builder (Vite builds to /app/dist)
COPY --from=builder /app/dist /usr/share/nginx/html

# Expose Nginx port
EXPOSE 80

# Start Nginx
CMD ["nginx", "-g", "daemon off;"]
