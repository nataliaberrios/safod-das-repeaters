#!/usr/bin/env python
"""Write the advisor checkpoint JSON/CSV from current clean-room evidence."""

from __future__ import annotations

import json

from .checkpoint import write_checkpoint


def main() -> None:
    payload = write_checkpoint()
    print(
        json.dumps(
            {
                "project_decision": payload["project_decision"],
                "highest_value_next_analysis": payload[
                    "highest_value_next_analysis"
                ],
                "network_model_sha256": payload["network_model_sha256"],
                "config_sha256": payload["config_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

