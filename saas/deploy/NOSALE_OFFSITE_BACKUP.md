# NoSale offsite PostgreSQL backup (E5)

Independent backup from other E5 projects. Does NOT replace the Docker-local
rolling `saas_pg_backups` volume.

At 02:15 local server time the root-owned systemd timer runs
`deploy/nosale-backup-offsite.sh`, which:
1. Takes an online PostgreSQL custom-format dump inside the NoSale DB container.
2. Copies the private SaaS environment (needed for decrypting encrypted tokens).
3. Checks SHA-256 locally; uses SSH/rsync to a dedicated offsite subtree; verifies
   SHA-256 remotely before atomically renaming the snapshot.
4. Retains completed snapshots 7 days locally / 21 days offsite.
   The existing shared E4 remote backup has its own retention policy: run daily.
5. Prints only a timestamp on success, never credentials, DB content or settings.

Root-only `/etc/nosale-saas/offsite.env` (mode 0600) must define
`NOSALE_OFFSITE_HOST`, `NOSALE_OFFSITE_USER`, `NOSALE_OFFSITE_SSH_KEY`,
`NOSALE_OFFSITE_REMOTE_ROOT`. Provision these on the **host**, not in Git.
Remote directory and its ancestors must be owned by the authorized backup user.
The existing trusted E5 offsite SSH identity is permitted for a separate NoSale
subtree but must not modify shared backup jobs, permissions or schedules.

First-run acceptance:
- `bash -n deploy/nosale-backup-offsite.sh`
- `systemctl start nosale-backup-offsite.service` returns 0
- `journalctl -u nosale-backup-offsite.service -n 15` shows `NOSALE_OFFSITE_VERIFIED`
- Check latest remote `SHA256SUMS` by SSH (script does this automatically)
- Restore a local PostgreSQL archive into an isolated temporary PostgreSQL server
  without any network to production (already proved on 2026-10-25)
- `systemctl list-timers nosale-backup-offsite.timer` is active.

Security: files include tokens, account data, keys, and personal data. Remote
offsite account must be access restricted, and secure independent credential
escrow/remote-disk encryption should be reviewed before commercial launch.
No public endpoint serves archives, and no WB/AI/Tochka service is contacted.
