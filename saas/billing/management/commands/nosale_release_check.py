"""Read-only commercial release checker; never calls WB, Tochka or AI."""
import json
from django.core.management.base import BaseCommand, CommandError
from billing.release_readiness import release_report

class Command(BaseCommand):
    help = "Report release blockers without modifying products, money, AI or seller data."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument("--require-public-go", action="store_true")

    def handle(self, *args, **opts):
        report = release_report()
        if opts["as_json"]:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            self.stdout.write(f"NoSale: {report['decision']}")
            self.stdout.write(f"Current product: {report['scope']}")
            for check in report["checks"]:
                self.stdout.write(
                    f"{'PASS' if check['ok'] else 'BLOCK'} "
                    f"{check['id']}: {check['evidence']}"
                )
            for proof in report["external_evidence"]:
                self.stdout.write(f"PROOF REQUIRED {proof['id']}: {proof['requirement']}")
            self.stdout.write("Public sale is NOT approved by this read-only check.")
        if opts["require_public_go"] and not report["public_go"]:
            raise CommandError("Public release NO-GO: external acceptance missing")
