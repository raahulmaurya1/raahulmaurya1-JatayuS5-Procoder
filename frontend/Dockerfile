# Build Stage
FROM node:20 AS builder

WORKDIR /app

# Install dependencies
# NOTE: Only copy package.json (not package-lock.json). The lockfile was generated
# on Windows and pins Windows-specific native binaries. We let npm resolve fresh
# Linux/Alpine-compatible packages inside the container.
COPY package.json ./
RUN npm install --os=linux --cpu=x64

# Inject API Base URL during build
ARG REACT_APP_API_BASE_URL
ENV REACT_APP_API_BASE_URL=$REACT_APP_API_BASE_URL

# Copy source code and build
COPY . .
RUN npm run build

# Serve Stage
FROM nginx:alpine
# Copy built assets from builder (CRA builds to /app/build)
COPY --from=builder /app/build /usr/share/nginx/html

# Expose Nginx port
EXPOSE 80

# Start Nginx
CMD ["nginx", "-g", "daemon off;"]
