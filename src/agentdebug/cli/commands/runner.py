"""Persistent HTTP runner service command."""

from __future__ import annotations

import os
import sys


def run(args) -> int:
    from agentdebug.rerun import HttpRunnerCapabilities, serve_http_runner

    token = None
    if args.token_env:
        token = os.environ.get(args.token_env)
        if not token:
            print(
                f'runner token environment variable is not set: {args.token_env}',
                file=sys.stderr,
            )
            return 2
    policies = args.checkpoint_policy or ['from_start']
    try:
        serve_http_runner(
            args.entrypoint,
            HttpRunnerCapabilities(
                runner=args.name,
                framework=args.framework,
                checkpoint_policies=tuple(policies),
                environment_restore=args.environment_restore,
                max_concurrency=args.max_concurrency,
            ),
            host=args.host,
            port=args.port,
            bearer_token=token,
        )
    except Exception as exc:
        print(f'runner service failed: {exc}', file=sys.stderr)
        return 5
    return 0


__all__ = ['run']
