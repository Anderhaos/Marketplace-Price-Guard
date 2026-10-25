# NoSale independent encrypted offsite backup (E5)

NoSale retains its original Docker-local rolling backup volume; this dedicated
systemd timer creates separate *encrypted* online PostgreSQL and SaaS environment
snapshots daily at approximately 02:15 server local time.

## Encryption and key custody

Format `nosale-openpgp-v1`: GnuPG public-key encryption of two independent
artifacts, `postgres.dump.gpg` and `saas.env.gpg`, plus `FORMAT` and
`SHA256SUMS` covering ciphertext and metadata. No PostgreSQL dump or .env is
stored as a plaintext file in the new local/offsite snapshot. The DB dump
stream goes directly from `docker exec pg_dump` to GPG.

- E5 contains a **public** key only under root-only
  `/etc/nosale-saas/gpg-public`.
- A **separate recovery machine** stores the private OpenPGP key, under a
  root-only GPG homedir, not on E5 or the offsite storage host.
- `NOSALE_OFFSITE_GPG_RECIPIENT` pins the complete 40-character verified
  fingerprint in the root-only host `/etc/nosale-saas/offsite.env`.
- Never commit secret keys, the environment file, DB dumps or tokens to Git.
- The owner must export an independently protected **offline copy of the
  private key** and test its restoration before commercial launch.
  Root-only storage on a third host is good separation but not durable escrow.
- The separate backup storage host receives ciphertext only for new snapshots.
  **Old snapshots before migration remain plaintext** until their original
  retention has elapsed or they are separately migrated and verified.

## Execution and verification

Service: `nosale-backup-offsite.service`, timer:
`nosale-backup-offsite.timer`. No changes to shared E4 jobs.

1. `bash -n saas/deploy/nosale-backup-offsite.sh`
2. Confirm public key fingerprint and that E5 has no secret keys.
3. Verify root-only service environment has the recipient fingerprint.
4. `systemctl start nosale-backup-offsite.service` and confirm it succeeds.
5. Newest snapshot contains only `postgres.dump.gpg`, `saas.env.gpg`,
   `FORMAT`, `SHA256SUMS`. Remote checksum verification occurs before
   atomic publish (old partial snapshots are never promoted).
6. Confirm the ciphertext can be decrypted **on the recovery machine** and
   that `pg_restore -l` accepts the decrypted PG dump stream; decrypt config
   into an isolated tmpfs with 0600 mode only if actual disaster recovery
   requires it. Avoid printing secrets to terminal logs.
7. `systemctl list-timers --all nosale-backup-offsite.timer`.

A failed recipient, gpg or SSH transport should abort nonzero and trigger the
host-side NoSale operations alert. The backup service fails **closed** instead
of falling back to plaintext uploads.

Local completed backup retention: 7 days; offsite: 21 days.
The original unencrypted offsite snapshot directories must not be represented
as encrypted. Do not remove legacy plaintext copies until an encrypted
snapshot and independent decryption drill have passed.

## Threat model and limitations

Asymmetric encryption protects new offsite data at rest if the storage account
is compromised without access to the private key. It does not eliminate the
need to protect E5 at runtime, to safeguard the recovery host, or to test
recovery key backups. SHA-256 detects transport corruption but is not a
substitute for the GPG integrity check when decrypting. A compromised E5 could
replace the encryption key; compare its fingerprint to the independently
recorded recovery public key before trusting newly written snapshots.

## Local PostgreSQL restore proof (R5.4)

For a root-owned, **local plaintext custom-format dump that is already available
to the E5 deployment administrator**, run:

```sh
bash saas/deploy/nosale-local-restore-drill.sh /srv/mpnosale-saas/secure-backups/<verified-backup>.dump
```

Repeat independently for the UI-preview archive. The command uses an existing
`postgres:16-alpine` image, no network, no exposed ports, a read-only bind
of the supplied dump, disposable tmpfs and Docker `--rm`. It verifies that
the archive restores into a new database and contains the expected NoSale
tables and migration metadata. It reports only schema counts; restored customer
records are not shown, exported, or stored permanently. The live database
containers and all service configuration stay untouched. The default 256 MiB
tmpfs is intentionally bounded; larger archives may need a reviewed procedure.

**Verified E5 drill on 2026-10-25 UTC**: the R5.3 release backups for NoSale
production and UI-preview each restored successfully into isolated PostgreSQL
16 containers; each contained 34 public tables and the Finance/Guard schema.
These runs were conducted before this command was committed and used the same
isolation pattern. They prove LOCAL dump recoverability only.

**Not yet proven**: restore directly from the encrypted offsite storage snapshot.
That requires reading the actual remote ciphertext and decrypting on the separate
recovery machine that holds the private key. NEVER copy the recovery private key
to E5 or treat local plaintext dump tests as proof of offsite disaster recovery.
This remains a prelaunch gate in issue #52.
