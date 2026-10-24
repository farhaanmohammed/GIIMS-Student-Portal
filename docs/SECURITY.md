# GIIMS Security Checklist

- [ ] Custom user model decided and created before initial migrations if required.
- [ ] Role permissions and centre-level access enforced server-side.
- [ ] CSRF protection enabled for normal and HTMX state-changing requests.
- [ ] Production `DEBUG=False`; secret key and credentials supplied through environment/secrets manager.
- [ ] Allowed hosts, HTTPS, secure cookies, trusted origins, and proxy configuration reviewed.
- [ ] Student document uploads validated and kept private.
- [ ] Exports and receipt downloads apply the same permissions as pages.
- [ ] No production data, secrets, or private uploads in Git.
- [ ] Logs avoid passwords, tokens, and unnecessary personal/financial data.
- [ ] Backups are protected and restoration is tested.
- [ ] Privileged actions and financial corrections are auditable.
- [ ] Dependencies and deployment configuration are reviewed for known security issues.
