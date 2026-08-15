#!/usr/bin/env python
"""Write the advisor checkpoint JSON/CSV from current v2 evidence."""

from __future__ import annotations

import json

from .checkpoint import write_checkpoint


def main() -> None:
    payload = write_checkpoint()
    print(json.dumps({
        "pilot_decision": payload["pilot_decision"],
        "verified_events_in_bounded_pilot": payload["verified_events_in_bounded_pilot"],
        "config_sha256": payload["config_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()

