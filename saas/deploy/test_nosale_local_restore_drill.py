"""Local restore proof must stay network-isolated and never use live DB connections."""
from pathlib import Path
import shutil
import subprocess
from unittest import TestCase, skipUnless

SCRIPT = Path(__file__).with_name("nosale-local-restore-drill.sh")


class RestoreDrillSafetyTests(TestCase):
    @skipUnless(shutil.which("bash"), "bash required")
    def test_shell_syntax_and_missing_archive_refusal(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True, timeout=8)
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True, text=True, timeout=8,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Usage:", result.stderr)

    def test_disposable_isolated_pg16_contract(self):
        content = SCRIPT.read_text(encoding="utf-8")
        required = (
            '--network none', '--rm', '--pull never', '--pids-limit',
            '--tmpfs /var/lib/postgresql/data', '--tmpfs /tmp',
            'target=/backup.dump,readonly', 'postgres:16-alpine',
            'listen_addresses=', '--exit-on-error', 'RESTORE_DRILL_VERIFIED',
            'guard_financesnapshot', '0018_finance_first_page',
            'if [[ ! -f "$SOURCE_INPUT" || -L "$SOURCE_INPUT"',
        )
        for clause in required:
            with self.subTest(clause=clause):
                self.assertIn(clause, content)
        self.assertNotIn('--network host', content)
        self.assertNotIn('docker exec mpnosale-saas-db', content)
        self.assertNotIn('POSTGRES_PASSWORD', content)
        self.assertNotIn('pg_restore -h db', content)
