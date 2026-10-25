"""Regression tests for NoSale's public-key encrypted offsite format."""
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless

SCRIPT = Path(__file__).with_name("nosale-backup-offsite.sh")


def gpg(home, *arguments, stdin=None):
    return subprocess.run(
        ["gpg", "--homedir", str(home), "--batch", "--yes", *arguments],
        input=stdin, capture_output=True, check=True, timeout=45,
    )


class EncryptedBackupContractTests(TestCase):
    def test_offsite_contract_requires_public_only_key_and_ciphertext(self):
        script = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "NOSALE_OFFSITE_GPG_RECIPIENT:?",
            'test "$actual_fpr" = "$NOSALE_OFFSITE_GPG_RECIPIENT"',
            '"$SNAP/postgres.dump.gpg"',
            '"$SNAP/saas.env.gpg"',
            'test ! -e postgres.dump',
            'test ! -e saas.env',
            'sha256sum -c SHA256SUMS',
            'if gpg --homedir "$GPG_HOME" --batch --list-secret-keys',
        ):
            self.assertIn(required, script)

    @skipUnless(shutil.which("gpg"), "GnuPG required for crypto integration test")
    def test_public_only_encryption_and_independent_private_key_recovery(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            private_home = base / "private"
            public_home = base / "public"
            private_home.mkdir(mode=0o700)
            public_home.mkdir(mode=0o700)
            identity = "NoSale Fixture <no-reply@example.test>"
            gpg(private_home, "--pinentry-mode", "loopback", "--passphrase", "",
                "--quick-generate-key", identity, "rsa2048", "encr", "0")
            key = gpg(private_home, "--armor", "--export", identity).stdout
            gpg(public_home, "--import", stdin=key)
            self.assertEqual(gpg(public_home, "--list-secret-keys", "--with-colons").stdout, b"")
            plaintext = b"synthetic database dump and fake config; no credentials"
            ciphertext = gpg(public_home, "--trust-model", "always",
                             "--recipient", identity, "--encrypt", stdin=plaintext).stdout
            self.assertNotIn(plaintext, ciphertext)
            cannot_decrypt = subprocess.run(
                ["gpg", "--homedir", str(public_home), "--batch", "--decrypt"],
                input=ciphertext, capture_output=True, timeout=30,
            )
            self.assertNotEqual(cannot_decrypt.returncode, 0)
            restored = gpg(private_home, "--decrypt", stdin=ciphertext).stdout
            self.assertEqual(restored, plaintext)
