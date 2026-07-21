# Dokploy Deployment Guide

## Deploy K.I.R.O Register on Dokploy

Dokploy uses single Docker container deployment (not docker-compose).

### 1. Create New Service

In Dokploy UI:
- **Service Type**: Application
- **Image Source**: Docker Image
- **Image**: `ghcr.io/YOUR_USERNAME/kiro-register:latest`

### 2. Configure Environment Variables

In Dokploy UI → **Environment** tab, add:

```
# Required: Mail Provider
MAIL_PROVIDER=gsuite_imap

# GSuite IMAP (if using)
GSUITE_IMAP_EMAIL=your-email@gmail.com
GSUITE_IMAP_PASSWORD=your-app-password
GSUITE_IMAP_SERVER=imap.gmail.com
GSUITE_IMAP_PORT=993

# OR ShiroMail (if using)
SHIROMAIL_API_KEY=your_api_key
SHIROMAIL_DOMAIN_ID=123

# OR YYDSMail (if using)
YYDSMAIL_API_KEY=your_api_key

# Required: 9router Settings
ROUTER9_URL=https://oapi.fastev.my.id
ROUTER9_PASSWORD=your_password

# Optional: Proxy
PROXY_URL=http://proxy:port
```

### 3. Configure Command

In Dokploy UI → **Command** section:

**Batch Mode (10 accounts then stop):**
```
--batch 10 --delay 10 --9router
```

**Service Mode (continuous):**
```
--service --delay 60 --9router
```

**Batch Loop Mode:**
```
--batch-loop 5 --account-delay 10 --batch-delay 120 --9router
```

### 4. Configure Volumes (optional)

In Dokploy UI → **Volumes** tab:

```
# Data directory (database, logs)
Host: /opt/kiro/data
Container: /data
```

### 5. Deploy

Click **Deploy** button. Monitor logs in Dokploy UI.

## Configuration Methods

### Option 1: Environment Variables (Recommended for Dokploy)

Set all values in Dokploy UI Environment tab. No config file needed.

**Pros:**
- Easy to update via UI
- No file editing
- Works without volume mounts

**Cons:**
- Sensitive values visible in UI

### Option 2: Config File + Volume Mount

Mount kiro_config.json via volume.

**Pros:**
- All settings in one file
- Can version control config

**Cons:**
- Need SSH access to edit
- Requires volume mount

**Note:** Environment variables override config file values.

## Environment Variable Reference

### Mail Provider Settings

**GSuite IMAP:**
```
MAIL_PROVIDER=gsuite_imap
GSUITE_IMAP_EMAIL=user@gmail.com
GSUITE_IMAP_PASSWORD=app-password
GSUITE_IMAP_SERVER=imap.gmail.com
GSUITE_IMAP_PORT=993
```

**ShiroMail:**
```
MAIL_PROVIDER=shiromail
SHIROMAIL_API_KEY=your_key
SHIROMAIL_DOMAIN_ID=123
```

**YYDSMail:**
```
MAIL_PROVIDER=yydsmail
YYDSMAIL_API_KEY=your_key
```

### 9router Settings

```
ROUTER9_URL=https://oapi.fastev.my.id
ROUTER9_PASSWORD=your_password
```

### Optional Settings

```
PROXY_URL=http://proxy:port
DOMAINS_PATH=/config/domains.txt  # If using GSuite IMAP domain pool
```

## Example: Complete Dokploy Setup

**Environment Variables in Dokploy UI:**
```
MAIL_PROVIDER=gsuite_imap
GSUITE_IMAP_EMAIL=aws-register@yourdomain.com
GSUITE_IMAP_PASSWORD=xxxx-xxxx-xxxx-xxxx
ROUTER9_URL=https://oapi.fastev.my.id
ROUTER9_PASSWORD=your_9router_password
```

**Command:**
```
--service --delay 60 --9router
```

**Volumes:**
```
/opt/kiro/data -> /data
```

**Resources:**
- CPU: 2 cores
- Memory: 2GB

## Troubleshooting

### Check Logs in Dokploy
Navigate to: `Dokploy UI → Service → Logs tab`

### Verify Environment Variables
```bash
# SSH to Dokploy host, then:
docker ps  # Find container ID
docker exec -it <container-id> env | grep -E 'MAIL|ROUTER9'
```

### Test Configuration
```bash
docker exec -it <container-id> python service.py --help
```

### Common Issues

**"Mail provider not configured":**
- Verify `MAIL_PROVIDER` env var exists
- Check provider-specific env vars (GSUITE_IMAP_*, SHIROMAIL_*, etc.)

**"9router config incomplete":**
- Verify `ROUTER9_URL` and `ROUTER9_PASSWORD` env vars

**Container restarts continuously:**
- Check logs for errors
- Verify all required env vars set
- Check command syntax

## Production Recommendations

1. **Pin image version**: Use `:v1.0.0` not `:latest`
2. **Set resource limits**: CPU and memory in Dokploy UI
3. **Enable auto-restart**: On in Dokploy settings
4. **Monitor logs**: Check regularly for errors
5. **Backup data**: `/data` directory contains accounts.db
6. **Use secrets**: For sensitive values (Dokploy supports secrets)

## Does Project Read .env File?

**No.** Project reads:
1. Environment variables (from Dokploy UI)
2. kiro_config.json (if mounted)

**.env file not supported.** Use Dokploy Environment tab instead.
