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

### Authentication

The MCP endpoint (`/mcp`) has **no authentication of its own**. Anyone who can reach
the port can operate the NetEase account behind `NETEASE_COOKIE`, including creating,
modifying and deleting playlists and changing red hearts.

- Bind to `127.0.0.1` (`MCP_HOST=127.0.0.1`) if only local clients need it.
- For remote use, put the server behind a reverse proxy that enforces HTTPS and
  authentication (VPN, basic auth, bearer token, network allow list, ...).
- Never expose the port directly to the internet.

### DNS rebinding / browser origin check

`/mcp` rejects requests whose `Origin` header is not `http://localhost[:port]`,
`http://127.0.0.1[:port]` or `http://[::1][:port]` unless the origin is listed in
`MCP_ALLOW_ORIGINS` (or `*`). This stops a random web page from driving your
account through `http://localhost:3456`. Non-browser clients usually send no
`Origin` and are unaffected.

### Cookie Handling

- The `NETEASE_COOKIE` environment variable contains session credentials
- Never commit `.env` files or expose cookies in logs
- The server does not log cookie values; error logs and error messages strip
  query strings and `MUSIC_U` / `__csrf` values
- Cookies are only sent to `music.163.com` endpoints

### Data Privacy

- This server accesses your personal NetEase Cloud Music account
- No data is stored locally (stateless design, sessions live in memory)
- No data is sent to third parties
- All API calls go directly to music.163.com

## Best Practices for Deployment

1. Use environment variables for all secrets
2. Run behind a reverse proxy with TLS and authentication
3. Restrict network access to trusted clients only
4. Rotate your NetEase cookie periodically
5. Monitor logs for unusual API call patterns
