# K.I.R.O Register - Docker Deployment

## Quick Start

### 1. Pull Image from GitHub Container Registry

```bash
# Login to GitHub Container Registry (if image is private)
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin

# Pull latest image
docker pull ghcr.io/GITHUB_USERNAME/kiro-register:latest

# Or specific version
docker pull ghcr.io/GITHUB_USERNAME/kiro-register:v1.0.0
```

### 2. Prepare Configuration

```bash
# Create directories
mkdir -p config data

# Copy your config files
cp kiro_config.json config/
cp domains.txt config/  # If using GSuite IMAP
```

### 3. Using Docker Compose (Recommended)

**Edit docker-compose.yml:**
```yaml
services:
  kiro-register:
    image: ghcr.io/GITHUB_USERNAME/kiro-register:latest
    # ... rest of config
```

```bash
# Start
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

### 4. Using Docker CLI

```bash
# Pull image
docker pull ghcr.io/<your-username>/kiro-register:latest

# Run batch mode
docker run -d \
  -v $(pwd)/config:/config:ro \
  -v $(pwd)/data:/data \
  --name kiro-register \
  ghcr.io/<your-username>/kiro-register:latest \
  --batch 10 --delay 10 --9router

# Run continuous mode
docker run -d \
  -v $(pwd)/config:/config:ro \
  -v $(pwd)/data:/data \
  --name kiro-register \
  ghcr.io/<your-username>/kiro-register:latest \
  --service --delay 60 --9router

# View logs
docker logs -f kiro-register

# Stop
docker stop kiro-register
docker rm kiro-register
```

## Configuration

### Method 1: Config Files (Recommended)

Mount `kiro_config.json` and `domains.txt`:

```yaml
volumes:
  - ./config:/config:ro
  - ./data:/data
```

### Method 2: Environment Variables

Override settings via environment variables:

```yaml
environment:
  # Mail provider
  - MAIL_PROVIDER=gsuite_imap
  - SHIROMAIL_API_KEY=your_key
  
  # GSuite IMAP
  - GSUITE_IMAP_EMAIL=user@gmail.com
  - GSUITE_IMAP_PASSWORD=app_password
  - DOMAINS_PATH=/config/domains.txt
  
  # 9router
  - ROUTER9_URL=https://oapi.fastev.my.id
  - ROUTER9_PASSWORD=your_password
  
  # Proxy
  - PROXY_URL=http://proxy:port
```

## Volumes

- `/config` - Configuration files (kiro_config.json, domains.txt)
- `/data` - Persistent data (accounts.db, logs)

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CONFIG_PATH` | `/config/kiro_config.json` | Path to config file |
| `DOMAINS_PATH` | `/config/domains.txt` | Path to domains file |
| `DB_PATH` | `/data/accounts.db` | Path to database |

## Examples

### Batch Registration (10 accounts)

```bash
docker run -d \
  -v $(pwd)/config:/config:ro \
  -v $(pwd)/data:/data \
  ghcr.io/<user>/kiro-register:latest \
  --batch 10 --delay 10 --9router
```

### Continuous Service Mode

```bash
docker run -d \
  -v $(pwd)/config:/config:ro \
  -v $(pwd)/data:/data \
  ghcr.io/<user>/kiro-register:latest \
  --service --delay 60 --9router
```

### Batch Loop (5 accounts per batch, infinite)

```bash
docker run -d \
  -v $(pwd)/config:/config:ro \
  -v $(pwd)/data:/data \
  ghcr.io/<user>/kiro-register:latest \
  --batch-loop 5 --account-delay 10 --batch-delay 120 --9router
```

## Troubleshooting

### GitHub Container Registry Access

**Private repository:**
```bash
# Create GitHub Personal Access Token (PAT) with read:packages scope
# https://github.com/settings/tokens

# Login
echo YOUR_GITHUB_TOKEN | docker login ghcr.io -u YOUR_USERNAME --password-stdin

# Pull
docker pull ghcr.io/YOUR_USERNAME/kiro-register:latest
```

**Public repository:** No login needed

### Image Not Found

```bash
# Check available tags
# Visit: https://github.com/YOUR_USERNAME/kiro-register/pkgs/container/kiro-register

# Or use GitHub API
curl -H "Authorization: token YOUR_TOKEN" \
  https://api.github.com/users/YOUR_USERNAME/packages/container/kiro-register/versions
```

### Check logs
```bash
docker logs -f kiro-register
```

### Verify config mounted
```bash
docker exec kiro-register ls -la /config
docker exec kiro-register cat /config/kiro_config.json
```

### Interactive shell
```bash
docker exec -it kiro-register /bin/bash
```

## GitHub Actions Auto-Build

Project includes GitHub Actions workflow that automatically builds and pushes Docker images:

**Triggers:**
- Push to `main` or `develop` branches
- Git tags matching `v*` (e.g., v1.0.0)
- Pull requests to `main`
- Manual workflow dispatch

**Image Tags:**
- `latest` - latest main branch
- `main` - main branch
- `develop` - develop branch
- `v1.0.0` - version tags
- `main-abc123` - commit SHA

**Example workflow (.github/workflows/build-image.yml):**
```yaml
name: Build Docker Image
on:
  push:
    branches: [main, develop]
    tags: ['v*']
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/build-push-action@v5
        with:
          push: true
          tags: ghcr.io/${{ github.repository }}:latest
```

## Production Deployment

### Using Docker Compose

```yaml
version: '3.8'

services:
  kiro-register:
    image: ghcr.io/YOUR_USERNAME/kiro-register:v1.0.0  # Pin to specific version
    container_name: kiro-register-prod
    restart: always
    volumes:
      - /opt/kiro/config:/config:ro
      - /opt/kiro/data:/data
    environment:
      - CONFIG_PATH=/config/kiro_config.json
      - DOMAINS_PATH=/config/domains.txt
    command: ["--service", "--delay", "60", "--9router"]
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

### Using Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kiro-register
spec:
  replicas: 1
  selector:
    matchLabels:
      app: kiro-register
  template:
    metadata:
      labels:
        app: kiro-register
    spec:
      containers:
      - name: kiro-register
        image: ghcr.io/YOUR_USERNAME/kiro-register:v1.0.0
        args: ["--service", "--delay", "60", "--9router"]
        volumeMounts:
        - name: config
          mountPath: /config
          readOnly: true
        - name: data
          mountPath: /data
        resources:
          limits:
            memory: "2Gi"
            cpu: "2000m"
      volumes:
      - name: config
        configMap:
          name: kiro-config
      - name: data
        persistentVolumeClaim:
          claimName: kiro-data
      imagePullSecrets:
      - name: ghcr-secret
---
apiVersion: v1
kind: Secret
metadata:
  name: ghcr-secret
type: kubernetes.io/dockerconfigjson
data:
  .dockerconfigjson: BASE64_ENCODED_DOCKER_CONFIG
```

### Create GitHub Token Secret

```bash
# Kubernetes
kubectl create secret docker-registry ghcr-secret \
  --docker-server=ghcr.io \
  --docker-username=YOUR_USERNAME \
  --docker-password=YOUR_GITHUB_TOKEN

# Docker Compose (using .env file)
echo "GITHUB_TOKEN=your_token_here" > .env
```

## Updating to Latest Version

```bash
# Pull latest
docker pull ghcr.io/YOUR_USERNAME/kiro-register:latest

# Restart with new image
docker-compose down
docker-compose up -d

# Or docker CLI
docker stop kiro-register
docker rm kiro-register
docker run -d \
  -v $(pwd)/config:/config:ro \
  -v $(pwd)/data:/data \
  --name kiro-register \
  ghcr.io/YOUR_USERNAME/kiro-register:latest \
  --service --delay 60 --9router
```

## Image Verification

```bash
# Check image digest
docker images --digests ghcr.io/YOUR_USERNAME/kiro-register

# Inspect image
docker inspect ghcr.io/YOUR_USERNAME/kiro-register:latest

# Check image layers
docker history ghcr.io/YOUR_USERNAME/kiro-register:latest
```

