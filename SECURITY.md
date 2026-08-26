# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 3.x     | :white_check_mark: |
| 2.x     | :x:                |
| 1.x     | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do NOT open a public issue**
2. Email the maintainer or use GitHub's private vulnerability reporting feature
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

## Security Considerations

### Cookie Handling
- The `NETEASE_COOKIE` environment variable contains session credentials
- Never commit `.env` files or expose cookies in logs
- The server does not log cookie values
- Cookies are only sent to `music.163.com` endpoints

### Network Security
- By default, the server binds to `0.0.0.0` for container deployments
- In production, use a reverse proxy with HTTPS
- No authentication is required by default - deploy behind a firewall or add your own auth layer

### Data Privacy
- This server accesses your personal NetEase Cloud Music account
- No data is stored locally (stateless design)
- No data is sent to third parties
- All API calls go directly to music.163.com

## Best Practices for Deployment

1. Use environment variables for all secrets
2. Run behind a reverse proxy with TLS
3. Restrict network access to trusted clients only
4. Rotate your NetEase cookie periodically
5. Monitor logs for unusual API call patterns
